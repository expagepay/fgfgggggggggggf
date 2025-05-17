import os
import re
import shutil
import logging
from flask import Flask, request, send_file, jsonify
import instaloader
import yt_dlp
import tempfile
import mimetypes # Para adivinhar o tipo de arquivo

# Configuração básica de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Diretório temporário global para downloads, se não estiver usando um por requisição
# BASE_DOWNLOAD_PATH = os.path.join(os.getcwd(), "tmp_downloads")
# os.makedirs(BASE_DOWNLOAD_PATH, exist_ok=True)

# --- Funções de Download ---

def download_instagram_post_or_story(url, temp_dir):
    L = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern='',
        storyitem_metadata_txt_pattern=''
    )
    # Tentar login anônimo (pode não ser necessário para posts públicos)
    # Para stories, o login é quase sempre necessário para downloads confiáveis.
    # Como o requisito é "apenas link", não faremos login persistente aqui.
    # try:
    #     L.load_session_from_file("seu_usuario_instagram") # Se você tivesse um login salvo
    # except:
    #     logger.warning("Não foi possível carregar a sessão do Instagram. Tentando anonimamente.")
    #     # L.login("seu_usuario", "sua_senha") # Não recomendado em código de produção sem gestão de segredos

    downloaded_files = []

    try:
        if "/stories/" in url:
            logger.info(f"Detectado Story do Instagram: {url}")
            # Ex: https://www.instagram.com/stories/username/3000000000000000000/
            match = re.search(r"/stories/([^/]+)/(\d+)", url)
            if not match:
                raise ValueError("URL de Story do Instagram inválida ou não suportada para download anônimo direto por ID.")

            username = match.group(1)
            # story_id = int(match.group(2)) # O ID específico do story é difícil de usar diretamente sem login
                                          # Instaloader baixa stories recentes do usuário

            logger.info(f"Tentando baixar stories de {username}")
            try:
                # Tenta baixar todos os stories do usuário e depois procuramos o específico (se possível)
                # Ou simplesmente pegamos o mais recente se o ID não for fácil de filtrar
                # Para downloads anônimos, pode ser que apenas alguns stories recentes sejam acessíveis
                profile = instaloader.Profile.from_username(L.context, username)
                for story in L.get_stories(userids=[profile.userid]):
                    for item in story.get_items():
                        # Tentativa de match pelo ID na URL (pode não ser 100% preciso)
                        # if str(item.mediaid) in url or str(item.id) in url:
                        # Para simplificar, vamos apenas baixar o que estiver disponível anonimamente
                        logger.info(f"Baixando story item: {item.date_utc}")
                        L.download_storyitem(item, target=f"#{username}_stories") # Baixa para uma subpasta com nome do usuário
                        # Mover os arquivos para o diretório temporário principal
                        story_path = os.path.join(os.getcwd(), f"#{username}_stories")
                        for f_name in os.listdir(story_path):
                            full_f_path = os.path.join(story_path, f_name)
                            if os.path.isfile(full_f_path) and (f_name.endswith(".jpg") or f_name.endswith(".mp4")):
                                new_path = os.path.join(temp_dir, f_name)
                                shutil.move(full_f_path, new_path)
                                downloaded_files.append(new_path)
                        shutil.rmtree(story_path, ignore_errors=True)
                        if downloaded_files: # Pega o primeiro story baixado e para
                            logger.info(f"Story baixado: {downloaded_files[0]}")
                            return downloaded_files # Retorna lista, mesmo que com um item
                if not downloaded_files:
                     raise Exception(f"Nenhum story encontrado ou acessível para {username} anonimamente.")
            except Exception as e:
                logger.error(f"Erro ao baixar stories de {username}: {e}")
                raise Exception(f"Falha ao baixar story do Instagram (pode requerer login ou ser privado): {e}")

        elif "/p/" in url or "/reel/" in url or "/tv/" in url:
            logger.info(f"Detectado Post/Reel/TV do Instagram: {url}")
            shortcode_match = re.search(r"/(p|reel|tv)/([^/]+)", url)
            if not shortcode_match:
                raise ValueError("Shortcode do Instagram não encontrado na URL.")
            shortcode = shortcode_match.group(2)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target="#instagram_post") # Baixa para uma subpasta temporária

            # Mover os arquivos para o diretório temporário principal
            post_download_path = os.path.join(os.getcwd(), "#instagram_post")
            for f_name in os.listdir(post_download_path):
                full_f_path = os.path.join(post_download_path, f_name)
                if os.path.isfile(full_f_path) and (f_name.endswith(".jpg") or f_name.endswith(".mp4")):
                    # Evitar arquivos de metadata ou txt
                    if not (f_name.endswith(".txt") or f_name.endswith(".json.xz")):
                        new_path = os.path.join(temp_dir, f_name)
                        shutil.move(full_f_path, new_path)
                        downloaded_files.append(new_path)
            shutil.rmtree(post_download_path, ignore_errors=True)

            if not downloaded_files:
                raise Exception("Nenhum arquivo de mídia encontrado no post do Instagram.")
            logger.info(f"Post do Instagram baixado: {downloaded_files}")
            return downloaded_files # Retorna uma lista de arquivos (para carrosséis)
        else:
            raise ValueError("URL do Instagram não reconhecida como Post ou Story.")

    except Exception as e:
        logger.error(f"Erro no Instaloader: {e}")
        raise Exception(f"Falha ao baixar do Instagram: {e}")


def download_tiktok_video(url, temp_dir):
    logger.info(f"Detectado vídeo do TikTok: {url}")
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        'format': 'best',
        'quiet': True,
        'noplaylist': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # ydl.prepare_filename pode retornar um nome com template, precisamos do real
            # A forma mais segura é listar o diretório após o download
            downloaded_file = None
            for f in os.listdir(temp_dir):
                if info.get('id') in f: # Procura pelo ID do vídeo no nome do arquivo
                    downloaded_file = os.path.join(temp_dir, f)
                    break
            if not downloaded_file or not os.path.exists(downloaded_file):
                 # Fallback se o ID não estiver no nome ou se o nome for complexo
                if 'requested_downloads' in info and info['requested_downloads']:
                     downloaded_file = info['requested_downloads'][0]['filepath']
                else: # Tenta encontrar o primeiro mp4
                    for f_name in os.listdir(temp_dir):
                        if f_name.endswith(".mp4") or f_name.endswith(".webm"): # TikTok pode ser webm
                            downloaded_file = os.path.join(temp_dir, f_name)
                            break
            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception(f"Arquivo baixado não encontrado em {temp_dir} após download do TikTok.")
            logger.info(f"Vídeo do TikTok baixado: {downloaded_file}")
            return [downloaded_file] # Retorna lista com um item
    except Exception as e:
        logger.error(f"Erro no yt-dlp para TikTok: {e}")
        raise Exception(f"Falha ao baixar vídeo do TikTok: {e}")

def download_youtube_video(url, temp_dir):
    logger.info(f"Detectado vídeo do YouTube: {url}")
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'), # Usar title para YouTube
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'noplaylist': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # filename = ydl.prepare_filename(info) # Pode não ser o nome final se houver merge
            downloaded_file = None
            # Após o download, o arquivo estará no temp_dir
            # O nome pode ser complexo, então listamos o dir
            for f_name in os.listdir(temp_dir):
                if f_name.endswith(".mp4") or f_name.endswith(".mkv") or f_name.endswith(".webm"):
                    downloaded_file = os.path.join(temp_dir, f_name)
                    break
            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception(f"Arquivo baixado não encontrado em {temp_dir} após download do YouTube.")
            logger.info(f"Vídeo do YouTube baixado: {downloaded_file}")
            return [downloaded_file] # Retorna lista com um item
    except Exception as e:
        logger.error(f"Erro no yt-dlp para YouTube: {e}")
        raise Exception(f"Falha ao baixar vídeo do YouTube: {e}")


# --- Rota da API ---
@app.route('/api/download', methods=['GET'])
def download_media():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Parâmetro 'url' é obrigatório."}), 400

    # Cria um diretório temporário único para esta requisição
    temp_dir = tempfile.mkdtemp(prefix="downloader_")
    logger.info(f"Diretório temporário criado: {temp_dir}")

    downloaded_files_paths = []
    error_message = None
    status_code = 200

    try:
        if "instagram.com" in url:
            downloaded_files_paths = download_instagram_post_or_story(url, temp_dir)
        elif "tiktok.com" in url:
            downloaded_files_paths = download_tiktok_video(url, temp_dir)
        elif "youtube.com" in url or "youtu.be" in url:
            downloaded_files_paths = download_youtube_video(url, temp_dir)
        else:
            error_message = "URL não suportada. Apenas Instagram, TikTok e YouTube são permitidos."
            status_code = 400

        if not error_message and not downloaded_files_paths:
            error_message = "Nenhum arquivo foi baixado. Verifique a URL ou o conteúdo pode ser privado/removido."
            status_code = 404 # Ou 500 se for uma falha inesperada

        if error_message:
             return jsonify({"error": error_message}), status_code

        # Se for um carrossel do Instagram, pode haver múltiplos arquivos.
        # Para esta API, vamos retornar apenas o primeiro arquivo encontrado (vídeo ou imagem).
        # Poderia ser expandido para retornar um zip ou uma lista de URLs para download.
        # Por simplicidade, vamos pegar o primeiro da lista que seja vídeo, senão a primeira imagem.
        # Ou simplesmente o primeiro arquivo da lista.
        if not downloaded_files_paths:
            return jsonify({"error": "Nenhum arquivo de mídia foi baixado."}), 500

        # Prioriza vídeo se houver múltiplos arquivos (ex: carrossel com vídeo e imagem)
        final_file_path = None
        for fp in downloaded_files_paths:
            if fp.lower().endswith(".mp4"):
                final_file_path = fp
                break
        if not final_file_path:
            final_file_path = downloaded_files_paths[0] # Pega o primeiro da lista

        if not os.path.exists(final_file_path):
             return jsonify({"error": f"Arquivo baixado não encontrado no servidor: {final_file_path}"}), 500

        # Advinha o mimetype para o cabeçalho Content-Type
        mimetype, _ = mimetypes.guess_type(final_file_path)
        if mimetype is None:
            # Fallback genérico
            mimetype = 'application/octet-stream'

        logger.info(f"Enviando arquivo: {final_file_path} com mimetype: {mimetype}")
        return send_file(
            final_file_path,
            as_attachment=True, # Força o download
            download_name=os.path.basename(final_file_path), # Nome do arquivo para o cliente
            mimetype=mimetype
        )

    except Exception as e:
        logger.exception("Erro durante o processamento da requisição de download")
        return jsonify({"error": f"Erro interno do servidor: {str(e)}"}), 500
    finally:
        # Limpa o diretório temporário e seu conteúdo
        if os.path.exists(temp_dir):
            logger.info(f"Limpando diretório temporário: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == '__main__':
    # Para Render, o Gunicorn usará a variável de ambiente PORT
    port = int(os.environ.get("PORT", 8080))
    # host='0.0.0.0' é importante para o Render
    app.run(host='0.0.0.0', port=port, debug=False) # debug=False para produção
