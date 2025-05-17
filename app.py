import os
import re
import shutil
import logging
import tempfile
import mimetypes
import subprocess # Para chamadas diretas ao ffmpeg se necessário (embora yt-dlp seja preferível)
from flask import Flask, request, send_file, jsonify
import instaloader
import yt_dlp
from yt_dlp.utils import DownloadError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Constantes ---
TMP_COOKIE_FILENAME = "cookies.txt"

# --- Funções Auxiliares ---
def create_temp_cookie_file(base_dir):
    """
    Cria um arquivo de cookies temporário se a variável de ambiente YOUTUBE_COOKIES_FILE_CONTENT estiver definida.
    Retorna o caminho para o arquivo de cookies ou None.
    """
    cookie_content = os.environ.get('YOUTUBE_COOKIES_FILE_CONTENT')
    if cookie_content:
        cookie_file_path = os.path.join(base_dir, TMP_COOKIE_FILENAME)
        try:
            with open(cookie_file_path, 'w') as f:
                f.write(cookie_content)
            logger.info(f"Arquivo de cookies temporário criado em: {cookie_file_path}")
            return cookie_file_path
        except Exception as e:
            logger.error(f"Erro ao criar arquivo de cookies temporário: {e}")
            return None
    return None

def extract_audio_from_video(video_path, output_dir, preferred_codec='mp3'):
    """
    Extrai áudio de um arquivo de vídeo usando yt-dlp (que usa ffmpeg).
    Retorna o caminho para o arquivo de áudio extraído ou None.
    """
    base, _ = os.path.splitext(os.path.basename(video_path))
    output_audio_path_template = os.path.join(output_dir, f"{base}.%(ext)s")

    ydl_opts_audio = {
        'quiet': False, # Mudar para True em prod
        'verbose': True, # Mudar para False em prod
        'outtmpl': output_audio_path_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': preferred_codec,
            'preferredquality': '192', # Qualidade do MP3 (ex: 128, 192, 320)
        }],
        'ffmpeg_location': '/usr/bin/ffmpeg', # Adicionar se o ffmpeg não estiver no PATH padrão no container
                                              # Geralmente está com a instalação via apt-get.
    }
    try:
        logger.info(f"Tentando extrair áudio de: {video_path} para formato {preferred_codec}")
        with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
            # Fornecemos o caminho do arquivo local para yt-dlp processar
            # yt-dlp não "baixa" aqui, ele apenas processa o arquivo local
            # Precisamos dar uma "URL" que ele entenda como arquivo local, ou info_dict.
            # A forma mais simples é simular um info_dict mínimo.
            # No entanto, yt-dlp pode não aceitar arquivos locais diretamente desta forma para post-processing.
            # Uma alternativa é usar subprocess diretamente com ffmpeg
            # Ou, mais simples: yt-dlp pode processar arquivos locais se passados como URL "file:"

            # Vamos tentar com subprocess para garantir controle, já que o vídeo já foi baixado.
            # output_filename = os.path.join(output_dir, f"{base}.{preferred_codec}")
            # command = [
            #     'ffmpeg', '-i', video_path,
            #     '-vn',  # Sem vídeo
            #     '-acodec', 'libmp3lame' if preferred_codec == 'mp3' else preferred_codec,
            #     '-ab', '192k', # Bitrate do áudio
            #     '-y', # Sobrescrever sem perguntar
            #     output_filename
            # ]
            # logger.info(f"Executando comando ffmpeg: {' '.join(command)}")
            # process = subprocess.run(command, capture_output=True, text=True, check=False)
            # if process.returncode == 0:
            #     logger.info(f"Áudio extraído com sucesso para: {output_filename}")
            #     return output_filename
            # else:
            #     logger.error(f"Erro ao extrair áudio com ffmpeg: {process.stderr}")
            #     return None

            # Tentativa com yt-dlp para processar arquivo local (mais simples se funcionar)
            # Precisamos dar um nome de arquivo de saída que não seja o template original
            # porque o template é para downloads da web.
            # A maneira mais fácil é usar yt-dlp para baixar DE NOVO mas apenas o áudio e do arquivo local.
            # Isso é um pouco redundante. A melhor forma é usar o 'postprocessors' em um download.
            # Mas como o vídeo já foi baixado (ex: Instagram), precisamos de extração pós-download.

            # A opção mais simples com yt-dlp para um arquivo já existente é forçar o processamento:
            # Criar um nome de saída explícito para o áudio
            audio_file_name_no_ext = os.path.join(output_dir, base)
            ydl_opts_audio_extract = {
                'outtmpl': audio_file_name_no_ext + '.%(ext)s', # Output no diretório correto
                'format': 'bestaudio/best', # Precisa de um 'format'
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': preferred_codec,
                    'preferredquality': '192',
                }],
                'quiet': False,
                'verbose': True,
                'ffmpeg_location': '/usr/bin/ffmpeg',
                'ignoreerrors': False
            }
            with yt_dlp.YoutubeDL(ydl_opts_audio_extract) as ydl_extract:
                # Passar o caminho do vídeo local como a "URL"
                # yt-dlp é inteligente o suficiente para tratar como arquivo local.
                ydl_extract.download([video_path])

            # Encontrar o arquivo de áudio gerado
            expected_audio_file = f"{audio_file_name_no_ext}.{preferred_codec}"
            if os.path.exists(expected_audio_file):
                logger.info(f"Áudio extraído com sucesso para: {expected_audio_file}")
                return expected_audio_file
            else: # Tentar encontrar por listagem se o nome não for exato
                for f_name in os.listdir(output_dir):
                    if f_name.startswith(base) and f_name.endswith(f".{preferred_codec}"):
                        found_path = os.path.join(output_dir, f_name)
                        logger.info(f"Áudio extraído encontrado por listagem: {found_path}")
                        return found_path
                logger.error(f"Arquivo de áudio {expected_audio_file} não encontrado após extração.")
                return None

    except Exception as e:
        logger.error(f"Erro durante a extração de áudio: {e}")
        return None

# --- Funções de Download (modificadas para aceitar `download_format` e `cookie_file_path`) ---

def download_instagram_post_or_story(url, temp_dir, download_format='video'):
    L = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern='',
        storyitem_metadata_txt_pattern=''
    )
    downloaded_media_files = [] # Pode ser vídeo ou imagem
    try:
        # ... (lógica de detecção de story/post como antes) ...
        # Supondo que a lógica anterior de instaloader baixe o vídeo/imagem para `downloaded_media_files`
        # Vou simplificar essa parte, pois já foi detalhada antes.
        # Apenas um exemplo de como seria o fluxo principal:
        is_video_downloaded = False
        temp_video_path = None

        if "/stories/" in url:
            # ... (lógica de download de story) ...
            # Se um story item (vídeo ou imagem) foi baixado, seu path estará em downloaded_media_files[0]
            # Por simplicidade, vamos assumir que o código anterior já preencheu downloaded_media_files
            logger.info(f"Processando story do Instagram: {url}")
            # Placeholder para a lógica de download do Instaloader (já implementada anteriormente)
            # Esta função agora retorna o caminho do arquivo baixado pelo Instaloader
            # ou levanta uma exceção.
            # Por ora, vamos simular que ela retorna um caminho para um vídeo se for um vídeo.
            # O código real do instaloader de antes deve ser usado aqui.
            # Simulando um retorno:
            # downloaded_media_files = ["/path/to/downloaded/story.mp4"] # ou .jpg

            # A lógica original do Instaloader para baixar stories/posts deve ser inserida aqui.
            # Ela deve popular `downloaded_media_files` com os caminhos dos arquivos.
            # Por exemplo, usando o código que você já tem:
            # (INÍCIO DO CÓDIGO INSTALOADER - RESUMIDO)
            logger.info(f"Detectado Story do Instagram: {url}")
            match = re.search(r"/stories/([^/]+)/(\d+)", url)
            username_match = re.search(r"instagram\.com/(?:stories/)?([^/]+)", url)

            if not username_match:
                raise ValueError("URL de Story do Instagram inválida ou nome de usuário não pôde ser extraído.")
            username = username_match.group(1)

            logger.info(f"Tentando baixar stories de {username}")
            profile = instaloader.Profile.from_username(L.context, username)
            story_found_and_downloaded = False
            for story in L.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    item_target_name = f"story_{item.owner_username}_{item.id}"
                    L.download_storyitem(item, target=item_target_name)
                    story_path_inst = os.path.join(os.getcwd(), item_target_name) # Instaloader baixa no CWD
                    for f_name in os.listdir(story_path_inst):
                        full_f_path = os.path.join(story_path_inst, f_name)
                        if os.path.isfile(full_f_path) and (f_name.endswith(".jpg") or f_name.endswith(".mp4")):
                            new_path = os.path.join(temp_dir, f_name) # Mover para o temp_dir da requisição
                            shutil.move(full_f_path, new_path)
                            downloaded_media_files.append(new_path)
                    shutil.rmtree(story_path_inst, ignore_errors=True)
                    if downloaded_media_files: # Pega o primeiro e para
                        story_found_and_downloaded = True
                        break
                if story_found_and_downloaded:
                    break
            if not story_found_and_downloaded:
                raise Exception(f"Nenhum story encontrado ou acessível para {username} anonimamente.")
            # (FIM DO CÓDIGO INSTALOADER STORY - RESUMIDO)

        elif "/p/" in url or "/reel/" in url or "/tv/" in url:
            # (INÍCIO DO CÓDIGO INSTALOADER POST - RESUMIDO)
            logger.info(f"Detectado Post/Reel/TV do Instagram: {url}")
            shortcode_match = re.search(r"/(p|reel|tv)/([^/]+)", url)
            if not shortcode_match:
                raise ValueError("Shortcode do Instagram não encontrado na URL.")
            shortcode = shortcode_match.group(2)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            post_target_name = f"post_{shortcode}"
            L.download_post(post, target=post_target_name) # Instaloader baixa no CWD
            post_download_path_inst = os.path.join(os.getcwd(), post_target_name)
            for f_name in os.listdir(post_download_path_inst):
                full_f_path = os.path.join(post_download_path_inst, f_name)
                if os.path.isfile(full_f_path) and (f_name.endswith(".jpg") or f_name.endswith(".mp4")):
                    if not (f_name.endswith(".txt") or f_name.endswith(".json.xz")):
                        new_path = os.path.join(temp_dir, f_name) # Mover para o temp_dir da requisição
                        shutil.move(full_f_path, new_path)
                        downloaded_media_files.append(new_path)
            shutil.rmtree(post_download_path_inst, ignore_errors=True)
            if not downloaded_media_files:
                raise Exception("Nenhum arquivo de mídia encontrado no post do Instagram.")
            # (FIM DO CÓDIGO INSTALOADER POST - RESUMIDO)
        else:
            raise ValueError("URL do Instagram não reconhecida como Post ou Story.")

        if not downloaded_media_files:
            raise Exception("Falha ao baixar mídia do Instagram.")

        # Agora, processar para MP3 se solicitado e se for vídeo
        final_output_files = []
        for media_file_path in downloaded_media_files:
            if media_file_path.lower().endswith((".mp4", ".mov")) and download_format == 'mp3':
                logger.info(f"Extraindo áudio MP3 do vídeo do Instagram: {media_file_path}")
                audio_path = extract_audio_from_video(media_file_path, temp_dir, 'mp3')
                if audio_path and os.path.exists(audio_path):
                    final_output_files.append(audio_path)
                    try:
                        os.remove(media_file_path) # Remover o vídeo original se o áudio foi extraído
                    except OSError as e:
                        logger.warning(f"Não foi possível remover o vídeo original {media_file_path}: {e}")
                else:
                    logger.warning(f"Falha ao extrair áudio de {media_file_path}, retornando vídeo original se permitido.")
                    # Se a extração falhar, podemos decidir não adicionar nada ou adicionar o vídeo original
                    # Por ora, não adicionamos se a extração falhou e era para ser mp3
            elif download_format == 'mp3' and media_file_path.lower().endswith((".jpg", ".jpeg", ".png")):
                logger.info(f"Solicitado MP3 de uma imagem do Instagram ({media_file_path}). Imagens não possuem áudio. Ignorando extração.")
                # Não faz sentido extrair áudio de imagem, mas o usuário pode ter pedido mp3.
                # Retornamos a imagem se o formato não for exclusivamente mp3,
                # ou nada se mp3 for o único formato esperado.
                # Para simplificar, se é MP3 e é imagem, não adiciona.
            else: # Formato de vídeo/imagem padrão
                final_output_files.append(media_file_path)

        if not final_output_files:
             raise Exception("Nenhum arquivo processado (verifique se o formato mp3 é aplicável ao conteúdo).")
        logger.info(f"Mídia do Instagram processada: {final_output_files}")
        return final_output_files

    except instaloader.exceptions.ProfileNotExistsException:
        raise Exception(f"Perfil do Instagram não encontrado ou privado.")
    except instaloader.exceptions.ConnectionException as e:
        if "Login required" in str(e) or "Redirected to login page" in str(e) or "401 Unauthorized" in str(e):
            raise Exception(f"Falha ao baixar do Instagram: Acesso negado ou requer login. O conteúdo pode ser privado ou o Instagram bloqueou o acesso anônimo.")
        raise Exception(f"Falha de conexão com o Instagram: {e}")
    except Exception as e:
        logger.error(f"Erro no Instaloader: {e}")
        raise Exception(f"Falha ao baixar do Instagram: {e}")


def download_tiktok_video(url, temp_dir, download_format='video', cookie_file_path=None):
    logger.info(f"TikTok: URL={url}, Formato={download_format}")
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        'quiet': False,
        'verbose': True,
        'noplaylist': True,
        'merge_output_format': 'mp4', # Tenta colocar num container mp4 se precisar de merge
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'retries': 3,
        'fragment_retries': 3,
    }
    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path

    if download_format == 'mp3':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        # Se estamos extraindo áudio, o merge_output_format para vídeo não é relevante
        if 'merge_output_format' in ydl_opts:
            del ydl_opts['merge_output_format']
    else: # Vídeo
        ydl_opts['format'] = 'bestvideo[ext=mp4][vcodec!*=av01]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/mp4/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = None
            # ... (lógica para encontrar `downloaded_file` como antes, adaptada para MP3 também)
            # Se for MP3, a extensão será .mp3
            expected_ext = f".{download_format}" if download_format == 'mp3' else ".mp4" # ou .webm etc. se mp4 falhar

            if 'requested_downloads' in info and info['requested_downloads']:
                downloaded_file = info['requested_downloads'][0].get('filepath')

            if not downloaded_file or not os.path.exists(downloaded_file):
                filename_from_info = info.get('_filename') or info.get('filename')
                if filename_from_info and os.path.exists(filename_from_info):
                    downloaded_file = filename_from_info
                else:
                    logger.warning("Não foi possível determinar o nome do arquivo do info_dict (TikTok). Listando.")
                    for f_name in os.listdir(temp_dir):
                        # Para MP3, o nome pode não ter o ID original, mas sim o título.
                        # E a extensão será mp3.
                        if download_format == 'mp3' and f_name.endswith(".mp3"):
                             downloaded_file = os.path.join(temp_dir, f_name)
                             break
                        # Para vídeo, procurar por ID no nome é mais confiável
                        elif download_format != 'mp3' and info.get('id') in f_name and \
                             (f_name.endswith(".mp4") or f_name.endswith(".webm")): # TikTok pode ser webm
                            downloaded_file = os.path.join(temp_dir, f_name)
                            break
                    if not downloaded_file: # Fallback mais genérico se o ID não estiver no nome
                        for f_name in os.listdir(temp_dir):
                            if f_name.endswith(expected_ext) or (download_format != 'mp3' and f_name.endswith(".webm")):
                                downloaded_file = os.path.join(temp_dir, f_name)
                                break

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception(f"Arquivo baixado ({expected_ext}) não encontrado em {temp_dir} após download do TikTok. Conteúdo: {os.listdir(temp_dir)}")

            logger.info(f"TikTok baixado/processado: {downloaded_file}")
            return [downloaded_file]
    except DownloadError as e:
        logger.error(f"Erro yt-dlp (TikTok): {e}")
        if "ffmpeg" in str(e).lower() and "not found" in str(e).lower():
            raise Exception(f"Falha TikTok: ffmpeg não encontrado, necessário para {download_format}.")
        raise Exception(f"Falha ao baixar/processar TikTok: {e}")
    except Exception as e:
        logger.error(f"Erro genérico TikTok: {e}")
        raise Exception(f"Falha TikTok: {e}")


def download_youtube_video(url, temp_dir, download_format='video', cookie_file_path=None):
    logger.info(f"YouTube: URL={url}, Formato={download_format}, Cookies={cookie_file_path is not None}")
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'quiet': False,
        'verbose': True,
        'noplaylist': True,
        'merge_output_format': 'mp4',
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'retries': 3,
        'fragment_retries': 3,
    }
    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path
        logger.info(f"Usando arquivo de cookies: {cookie_file_path}")


    if download_format == 'mp3':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        if 'merge_output_format' in ydl_opts:
            del ydl_opts['merge_output_format']
    else: # Vídeo
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = None
            # ... (lógica para encontrar `downloaded_file` como antes, adaptada para MP3 também) ...
            expected_ext = f".{download_format}" if download_format == 'mp3' else ".mp4"

            if 'requested_downloads' in info and info['requested_downloads']:
                downloaded_file = info['requested_downloads'][0].get('filepath')

            if not downloaded_file or not os.path.exists(downloaded_file):
                filename_from_info = info.get('_filename') or info.get('filename')
                if filename_from_info and os.path.exists(filename_from_info):
                    downloaded_file = filename_from_info
                else:
                    logger.warning("Não foi possível determinar o nome do arquivo do info_dict (YouTube). Listando.")
                    # Para MP3 ou vídeo, o nome é baseado no título.
                    # Listar o diretório e pegar o arquivo com a extensão correta.
                    for f_name in os.listdir(temp_dir):
                        if f_name.endswith(expected_ext) or \
                           (download_format != 'mp3' and (f_name.endswith(".mkv") or f_name.endswith(".webm"))):
                            downloaded_file = os.path.join(temp_dir, f_name)
                            break

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception(f"Arquivo baixado ({expected_ext}) não encontrado em {temp_dir} após download do YouTube. Conteúdo: {os.listdir(temp_dir)}")

            logger.info(f"YouTube baixado/processado: {downloaded_file}")
            return [downloaded_file]
    except DownloadError as e:
        logger.error(f"Erro yt-dlp (YouTube): {e}")
        if "Sign in to confirm you're not a bot" in str(e):
            raise Exception(f"Falha YouTube: Requer login (detecção de bot ou restrição). Tente configurar cookies.")
        if "ffmpeg" in str(e).lower() and "not found" in str(e).lower():
            raise Exception(f"Falha YouTube: ffmpeg não encontrado, necessário para {download_format}.")
        if "Private video" in str(e) or "Video unavailable" in str(e):
            raise Exception(f"Falha YouTube: Vídeo privado ou indisponível.")
        raise Exception(f"Falha ao baixar/processar YouTube: {e}")
    except Exception as e:
        logger.error(f"Erro genérico YouTube: {e}")
        raise Exception(f"Falha YouTube: {e}")

@app.route('/')
def health_check():
    return "API Media Downloader is healthy!", 200

# --- Rota da API Principal ---
@app.route('/api/download', methods=['GET'])
def download_media_route(): # Renomeada para evitar conflito com um possível módulo 'download_media'
    url = request.args.get('url')
    # Novo parâmetro para formato: 'video' (default), 'image', 'mp3'
    download_format_req = request.args.get('format', 'video').lower()

    if not url:
        return jsonify({"error": "Parâmetro 'url' é obrigatório."}), 400
    if download_format_req not in ['video', 'image', 'mp3']:
        return jsonify({"error": "Parâmetro 'format' inválido. Use 'video', 'image' ou 'mp3'."}), 400

    temp_dir = tempfile.mkdtemp(prefix="downloader_")
    logger.info(f"Req ID: {os.path.basename(temp_dir)} - URL: {url}, Formato: {download_format_req}")

    downloaded_files_paths = []
    error_message = None
    status_code = 200
    cookie_file_for_yt = None

    try:
        # Criar arquivo de cookies temporário para esta requisição (se YOUTUBE_COOKIES_FILE_CONTENT estiver definido)
        # Apenas para YouTube, por enquanto, mas poderia ser estendido.
        if "youtube.com" in url or "youtu.be" in url:
            cookie_file_for_yt = create_temp_cookie_file(temp_dir)

        if "instagram.com" in url:
            # Instagram: 'image' ou 'video' se for post/story com vídeo. 'mp3' se for vídeo.
            actual_ig_format = 'video' # Default para instaloader
            if download_format_req == 'image':
                actual_ig_format = 'image' # Se o usuário pedir imagem e for imagem, ótimo.
            elif download_format_req == 'mp3':
                actual_ig_format = 'mp3' # Passamos 'mp3' para a função lidar com extração.

            downloaded_files_paths = download_instagram_post_or_story(url, temp_dir, actual_ig_format)
        elif "tiktok.com" in url:
            downloaded_files_paths = download_tiktok_video(url, temp_dir, download_format_req, cookie_file_for_yt)
        elif "youtube.com" in url or "youtu.be" in url:
            downloaded_files_paths = download_youtube_video(url, temp_dir, download_format_req, cookie_file_for_yt)
        else:
            error_message = "URL não suportada. Apenas Instagram, TikTok e YouTube."
            status_code = 400

        if not error_message and not downloaded_files_paths:
            error_message = "Nenhum arquivo foi baixado/processado. Verifique URL ou conteúdo."
            status_code = 404 # Ou 500

        if error_message:
             return jsonify({"error": error_message}), status_code

        if not downloaded_files_paths: # Checagem final
            return jsonify({"error": "Nenhum arquivo de mídia retornado após processamento."}), 500

        # Selecionar o arquivo a ser enviado (geralmente o primeiro da lista)
        # A lógica de prioridade já deve ter sido aplicada nas funções de download/extração
        final_file_path = downloaded_files_paths[0]

        if not os.path.exists(final_file_path):
             return jsonify({"error": f"Arquivo final não encontrado: {final_file_path}"}), 500

        mimetype, _ = mimetypes.guess_type(final_file_path)
        if mimetype is None: # Fallbacks
            if final_file_path.lower().endswith(".mp4"): mimetype = "video/mp4"
            elif final_file_path.lower().endswith(".mp3"): mimetype = "audio/mpeg"
            elif final_file_path.lower().endswith((".jpg", ".jpeg")): mimetype = "image/jpeg"
            elif final_file_path.lower().endswith(".png"): mimetype = "image/png"
            else: mimetype = 'application/octet-stream'

        logger.info(f"Req ID: {os.path.basename(temp_dir)} - Enviando arquivo: {final_file_path}, mimetype: {mimetype}")
        return send_file(
            final_file_path,
            as_attachment=True,
            download_name=os.path.basename(final_file_path),
            mimetype=mimetype
        )

    except Exception as e:
        logger.exception(f"Req ID: {os.path.basename(temp_dir)} - Erro no processamento da API")
        client_error_message = str(e) # Por agora, retornamos a mensagem da exceção
        # Em produção, você pode querer mensagens mais genéricas para erros 500
        if status_code == 200 : status_code = 500 # Se nenhum status de erro foi definido, é interno
        if "Falha ao baixar" in client_error_message or "requer login" in client_error_message or "não encontrado" in client_error_message or "URL não suportada" in client_error_message or "privado ou indisponível" in client_error_message:
            if "login" in client_error_message or "privado" in client_error_message: status_code = 403 # Forbidden
            else: status_code = 400 # Bad request ou Not Found
        else:
            # Para erros inesperados, não vazar detalhes em produção real
            # client_error_message = "Ocorreu um erro interno ao processar sua solicitação."
            status_code = 500

        return jsonify({"error": client_error_message}), status_code
    finally:
        if cookie_file_for_yt and os.path.exists(cookie_file_for_yt):
            try:
                os.remove(cookie_file_for_yt)
                logger.info(f"Req ID: {os.path.basename(temp_dir)} - Arquivo de cookies temporário removido.")
            except OSError as e:
                logger.error(f"Req ID: {os.path.basename(temp_dir)} - Erro ao remover cookie temp: {e}")
        if os.path.exists(temp_dir):
            logger.info(f"Req ID: {os.path.basename(temp_dir)} - Limpando diretório: {temp_dir}")
            try:
                shutil.rmtree(temp_dir)
            except Exception as e_rm:
                logger.error(f"Req ID: {os.path.basename(temp_dir)} - Erro ao limpar tmp_dir {temp_dir}: {e_rm}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    # Para desenvolvimento local com Docker, você pode querer definir YOUTUBE_COOKIES_FILE_CONTENT
    # Ex: export YOUTUBE_COOKIES_FILE_CONTENT=$(cat /caminho/para/seus/cookies.txt)
    # app.debug = True # Cuidado com debug em produção
    app.run(host='0.0.0.0', port=port, debug=False)
