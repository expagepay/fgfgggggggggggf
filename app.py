import os
import re
import shutil
import logging
import tempfile
import mimetypes
import zipfile # Para criar arquivos ZIP
from flask import Flask, request, send_file, jsonify, make_response
import instaloader
import yt_dlp
from yt_dlp.utils import DownloadError

# Configuração do Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Constantes ---
TMP_COOKIE_FILENAME_YT = "cookies_yt.txt"
TMP_SESSION_FILENAME_IG = "instagram_session.session"
DOWNLOAD_TARGET_DIR_IG = "instagram_downloads" # Subdiretório dentro do temp_dir para downloads do IG

# --- Funções Auxiliares ---

def create_temp_file_from_env(env_var_name, filename_in_temp_dir, base_dir):
    """Cria um arquivo temporário a partir do conteúdo de uma variável de ambiente."""
    content = os.environ.get(env_var_name)
    if content:
        file_path = os.path.join(base_dir, filename_in_temp_dir)
        try:
            with open(file_path, 'w') as f:
                f.write(content)
            logger.info(f"Arquivo temporário '{filename_in_temp_dir}' criado em: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Erro ao criar arquivo temporário '{filename_in_temp_dir}': {e}")
    return None

def extract_audio_from_video_yt_dlp(video_path, output_dir, preferred_codec='mp3'):
    """Extrai áudio usando yt-dlp e FFmpeg."""
    base, _ = os.path.splitext(os.path.basename(video_path))
    # O output_template é relativo ao CWD do yt-dlp, que será o output_dir se o especificarmos.
    # No entanto, para processamento local, é mais seguro dar o caminho completo.
    audio_file_name_no_ext = os.path.join(output_dir, base)

    ydl_opts_audio_extract = {
        'outtmpl': audio_file_name_no_ext + '.%(ext)s',
        'format': 'bestaudio/best', # Precisa de um 'format' para iniciar o processamento
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': preferred_codec,
            'preferredquality': '192', # 192 kbps é uma boa qualidade para MP3
        }],
        'quiet': False, # Mudar para True em produção
        'verbose': True, # Mudar para False em produção
        'ffmpeg_location': '/usr/bin/ffmpeg', # Definido no Dockerfile
        'ignoreerrors': False,
        'writedescription': False, 'writeinfojson': False, 'writethumbnail': False, 'writeannotations': False,
        'writesubtitles': False, 'writeautomaticsub': False,
    }
    try:
        logger.info(f"Extraindo áudio de '{video_path}' para formato '{preferred_codec}' em '{output_dir}'")
        with yt_dlp.YoutubeDL(ydl_opts_audio_extract) as ydl_extract:
            ydl_extract.download([video_path]) # Passa o arquivo local como "URL"

        expected_audio_file = f"{audio_file_name_no_ext}.{preferred_codec}"
        if os.path.exists(expected_audio_file):
            logger.info(f"Áudio extraído com sucesso para: {expected_audio_file}")
            return expected_audio_file
        else: # Tentar encontrar por listagem se o nome não for exato (raro com outtmpl explícito)
            for f_name in os.listdir(output_dir):
                if f_name.startswith(base) and f_name.endswith(f".{preferred_codec}"):
                    found_path = os.path.join(output_dir, f_name)
                    logger.info(f"Áudio extraído encontrado por listagem: {found_path}")
                    return found_path
            logger.error(f"Arquivo de áudio '{expected_audio_file}' não encontrado após extração. Conteúdo de '{output_dir}': {os.listdir(output_dir)}")
            return None
    except Exception as e:
        logger.exception(f"Erro durante a extração de áudio de '{video_path}'")
        return None

def create_zip_from_files(file_paths, zip_filename_base, base_dir_for_zip):
    """Cria um arquivo ZIP a partir de uma lista de caminhos de arquivos."""
    zip_filepath = os.path.join(base_dir_for_zip, f"{zip_filename_base}.zip")
    try:
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in file_paths:
                if os.path.exists(file_path):
                    zipf.write(file_path, os.path.basename(file_path))
                    logger.info(f"Adicionado ao ZIP: {os.path.basename(file_path)}")
                else:
                    logger.warning(f"Arquivo não encontrado para adicionar ao ZIP: {file_path}")
        logger.info(f"Arquivo ZIP criado: {zip_filepath}")
        return zip_filepath
    except Exception as e:
        logger.exception(f"Erro ao criar arquivo ZIP '{zip_filepath}'")
        return None

# --- Funções de Download do Instagram ---

def get_instaloader_instance(temp_dir_for_session_management):
    """Inicializa e tenta logar o Instaloader."""
    L = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False, # Geralmente não queremos thumbs separados
        save_metadata=False,             # Não precisamos dos arquivos JSON/TXT de metadados
        compress_json=False,
        post_metadata_txt_pattern="",    # Evita arquivos .txt
        storyitem_metadata_txt_pattern="",
        dirname_pattern=os.path.join(temp_dir_for_session_management, DOWNLOAD_TARGET_DIR_IG, "{profile}") # Baixa para subpasta
    )
    # Prioridade 1: Carregar sessão do conteúdo da variável de ambiente
# Dentro de get_instaloader_instance:
    ig_session_content = os.environ.get('INSTAGRAM_SESSION_FILE_CONTENT')
    ig_username_for_session = os.environ.get('INSTAGRAM_USERNAME') # Este é o SEU usuário

    # L já foi inicializado
    # L = instaloader.Instaloader(...)

# Dentro de get_instaloader_instance
if ig_session_content and ig_username_for_session:
    session_filepath = os.path.join(temp_dir_for_session_management, f"{ig_username_for_session}.session")
    try:
        with open(session_filepath, 'w') as f:
            f.write(ig_session_content)
        
        logger.info(f"Instagram: Tentando carregar sessão para '{ig_username_for_session}' do arquivo '{session_filepath}'.")
        L.context.username = ig_username_for_session # Definir ANTES de carregar
        L.context.load_session_from_file(username=ig_username_for_session, filename=session_filepath) # Correto

        if L.context.is_logged_in and L.context.username == ig_username_for_session:
            logger.info(f"Instagram: Sessão carregada com SUCESSO para '{L.context.username}' a partir de var de ambiente.")
            return L
        else:
            # Se não logou ou o username não bate, a sessão não foi válida.
            logger.warning(f"Instagram: Sessão carregada de ENV mas o contexto indica que não está logado corretamente (is_logged_in: {L.context.is_logged_in}, context.username: {L.context.username}, expected_username: {ig_username_for_session}).")
            # Não retorna L, vai tentar login normal ou anônimo.

    except Exception as e:
        logger.warning(f"Instagram: Falha ao carregar sessão de ENV para '{ig_username_for_session}' (Exceção: {type(e).__name__} - {e}). Tentando login normal se configurado.")
        if os.path.exists(session_filepath): os.remove(session_filepath)
    
    # Se a carga da sessão falhou ou não foi configurada, prossegue para tentativa de login por user/pass
    # ... (resto da função como estava antes, para login por username/password) ...

    # Prioridade 2: Login com username/password de variáveis de ambiente
    username = os.environ.get('INSTAGRAM_USERNAME')
    password = os.environ.get('INSTAGRAM_PASSWORD')
    if username and password:
        try:
            L.login(username, password)
            logger.info(f"Instagram: Login bem-sucedido como '{username}'.")
            # Não vamos salvar a sessão de volta para env vars, é complexo e pode não ser desejado.
            # O usuário pode gerar um session file localmente e colocar em INSTAGRAM_SESSION_FILE_CONTENT.
            return L
        except Exception as e:
            logger.warning(f"Instagram: Login falhou para '{username}': {e}. Prosseguindo anonimamente se possível.")
    else:
        logger.info("Instagram: Nenhuma credencial (sessão ou login) fornecida. Prosseguindo anonimamente.")
    return L

def _download_instagram_items(L, items_iterator, target_profile_name, temp_download_dir):
    """Função genérica para baixar itens de um iterador do Instaloader."""
    downloaded_files = []
    # O dirname_pattern do Instaloader já coloca numa subpasta {profile}
    # Precisamos garantir que o target_profile_name seja usado corretamente
    # Instaloader baixa para CWD/{dirname_pattern}
    # Vamos direcionar para uma subpasta específica dentro do temp_download_dir geral da requisição

    # Certifique-se de que o diretório de destino específico do Instaloader dentro de temp_download_dir existe.
    # O dirname_pattern do L já deve cuidar disso, mas vamos garantir.
    # A lógica de mover arquivos do CWD para temp_dir foi removida porque
    # o dirname_pattern agora aponta para dentro do temp_dir da requisição.

    specific_ig_dl_path = os.path.join(temp_download_dir, DOWNLOAD_TARGET_DIR_IG, target_profile_name)
    os.makedirs(specific_ig_dl_path, exist_ok=True)
    L.dirname_pattern = os.path.join(DOWNLOAD_TARGET_DIR_IG, target_profile_name) # Relativo ao temp_download_dir

    original_cwd = os.getcwd()
    os.chdir(temp_download_dir) # Mudar CWD para o diretório temporário da requisição
                                # para que o Instaloader baixe dentro dele.

    try:
        for item in items_iterator:
            try:
                if isinstance(item, instaloader.StoryItem) or isinstance(item, instaloader.Post):
                    # Para stories, o target é o nome do dono do story.
                    # Para posts, o target é o shortcode.
                    # O dirname_pattern já define o {profile}, então o target aqui é mais para o nome do arquivo.
                    # No entanto, o dirname_pattern já está tratando o {profile}
                    # L.download_storyitem(item, target=item.owner_username) if isinstance(item, instaloader.StoryItem) else L.download_post(item, target=item.shortcode)
                    # A chamada de download deve ser genérica ou o Instaloader cuida disso.
                    # Usar download_item que é mais genérico.
                    if isinstance(item, instaloader.StoryItem):
                        L.download_storyitem(item, item.owner_username)
                    elif isinstance(item, instaloader.Post):
                        L.download_post(item, item.shortcode)
                    else: # Destaques, etc.
                         L.download_pic(filename_prefix=f"{item.date_utc.strftime('%Y-%m-%d_%H-%M-%S')}_{item.mediaid}", url=item.video_url if item.is_video else item.url, mtime=item.date_utc)

            except instaloader.exceptions.QueryReturnedNotFoundException:
                logger.warning(f"Item não encontrado durante o download (pode ter sido excluído): {item}")
                continue
            except Exception as e_item:
                logger.error(f"Erro ao baixar item individual do Instagram {item}: {e_item}")
                continue
        
        # Após todos os downloads, coletar os arquivos da subpasta específica.
        # O dirname_pattern foi: os.path.join(DOWNLOAD_TARGET_DIR_IG, target_profile_name)
        # E estávamos no temp_download_dir.
        # Então os arquivos estão em: temp_download_dir / DOWNLOAD_TARGET_DIR_IG / target_profile_name
        
        target_path_abs = os.path.join(temp_download_dir, DOWNLOAD_TARGET_DIR_IG, target_profile_name)

        if os.path.exists(target_path_abs):
            for f_name in os.listdir(target_path_abs):
                if f_name.endswith((".jpg", ".jpeg", ".png", ".mp4", ".mov")):
                    downloaded_files.append(os.path.join(target_path_abs, f_name))
        else:
            logger.warning(f"Diretório de download do Instaloader não encontrado: {target_path_abs}")


    finally:
        os.chdir(original_cwd) # Restaurar CWD

    if not downloaded_files:
        logger.info(f"Nenhum item baixado para {target_profile_name} a partir do iterador.")
    return downloaded_files


def download_instagram_content(L, url_or_username, temp_dir, download_format='video', ig_action=None):
    """Função principal para lidar com downloads do Instagram."""
    downloaded_files = []
    output_filename_base = "instagram_media" # Base para o nome do ZIP

    try:
        # Determinar a ação e o alvo (username ou shortcode)
        profile_username = None
        post_shortcode = None
        is_story_url = False
        is_highlight_url = False # Não há URL direta para todos os highlights, é por usuário

        if ig_action: # Ação explícita (geralmente com username)
            profile_username = url_or_username # Assumimos que url_or_username é o username aqui
            output_filename_base = f"{profile_username}_{ig_action}"
        else: # Tentar inferir da URL
            username_match = re.search(r"instagram\.com/([^/]+)/?(?:stories|saved|tagged|feed)?/?$", url_or_username)
            post_match = re.search(r"instagram\.com/(?:p|reel|tv)/([^/]+)", url_or_username)
            story_match = re.search(r"instagram\.com/stories/([^/]+)/(\d+)", url_or_username) # Story específico
            highlight_match = re.search(r"instagram\.com/stories/highlights/(\d+)", url_or_username) # Highlight específico (álbum)

            if story_match:
                profile_username = story_match.group(1)
                story_id = story_match.group(2)
                ig_action = 'story_item'
                output_filename_base = f"{profile_username}_story_{story_id}"
            elif highlight_match:
                highlight_id = highlight_match.group(1)
                ig_action = 'highlight_album'
                output_filename_base = f"highlight_{highlight_id}"
                # Precisaremos do Profile para obter o username do dono do highlight se não estiver na URL
            elif post_match:
                post_shortcode = post_match.group(1)
                ig_action = 'post'
                output_filename_base = f"post_{post_shortcode}"
            elif username_match: # URL de perfil genérica, ex: instagram.com/username/
                profile_username = username_match.group(1)
                # Se nenhuma ação específica (como story) foi detectada, e é URL de perfil,
                # o usuário precisa especificar ig_action, ou podemos ter um default (ex: ultimos posts)
                # Por enquanto, se for URL de perfil sem ig_action, não fazemos nada específico.
                if not ig_action: # Precisa de uma ação explícita para URL de perfil
                    raise ValueError("Para URL de perfil do Instagram, por favor, especifique 'ig_action' (profile_pic, stories, highlights).")
                output_filename_base = f"{profile_username}_{ig_action or 'content'}"
            else:
                raise ValueError("URL do Instagram não reconhecida ou 'ig_action' ausente para nome de usuário.")

        # Obter o objeto Profile se tivermos username
        profile = None
        if profile_username:
            try:
                profile = instaloader.Profile.from_username(L.context, profile_username)
            except instaloader.exceptions.ProfileNotExistsException:
                raise Exception(f"Perfil do Instagram '{profile_username}' não encontrado.")

        # Executar a ação de download
        items_to_download_iter = []

        if ig_action == 'profile_pic' and profile:
            pic_url = profile.profile_pic_url
            # L.download_pic já espera um diretório via dirname_pattern
            # Ele baixa para CWD / dirname_pattern / filename
            # Vamos controlar o nome do arquivo.
            filename = os.path.join(temp_dir, DOWNLOAD_TARGET_DIR_IG, profile_username, f"{profile_username}_profile_pic.jpg")
            os.makedirs(os.path.dirname(filename), exist_ok=True)

            original_cwd = os.getcwd()
            os.chdir(temp_dir) # Para L.download_pic funcionar dentro do temp_dir
            try:
                L.download_pic(filename=os.path.join(DOWNLOAD_TARGET_DIR_IG, profile_username, f"{profile_username}_profile_pic.jpg"),
                               url=pic_url, mtime=profile. στιλ) # στιλ é um placeholder para mtime
                downloaded_files.append(filename)
            finally:
                os.chdir(original_cwd)

        elif ig_action == 'stories' and profile:
            items_to_download_iter = L.get_stories(userids=[profile.userid])
        elif ig_action == 'highlights' and profile:
            items_to_download_iter = L.get_highlights(user=profile)
        elif ig_action == 'story_item' and profile: # Story específico por ID
            story_media_id_to_find = int(story_id) # Da URL
            found_item = None
            for story in L.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    if item.mediaid == story_media_id_to_find:
                        found_item = item
                        break
                if found_item: break
            if found_item: items_to_download_iter = [found_item] # Trata como iterável de um item
            else: raise Exception(f"Story específico com ID {story_id} não encontrado para {profile_username}.")
        elif ig_action == 'highlight_album' and highlight_id: # Destaque específico
            # Precisamos encontrar o StoryItem do highlight. Instaloader não tem get_highlight_by_id direto.
            # Teríamos que iterar pelos highlights do usuário se soubéssemos o usuário, ou ter o objeto Highlight
            # Esta parte é complexa sem o nome de usuário. Vamos simplificar:
            # Se a URL for de um highlight específico, o Instaloader geralmente consegue lidar com o download
            # do "post" associado a esse highlight_id, que é um StoryItem.
            # Precisaremos testar como o Instaloader trata URLs de highlights diretamente.
            # Por ora, vamos assumir que se a URL é de um highlight, L.download_post ou similar pode funcionar.
            # Esta é uma área que pode precisar de mais pesquisa com Instaloader.
            # Para simplificar, o download de "álbuns de destaque" individuais é melhor feito
            # baixando TODOS os destaques e o usuário escolhe, ou se o usuário fornece o nome do destaque.
            # Como temos o ID do highlight, podemos tentar construir um objeto Story (complexo) ou
            # focar no download de todos os highlights do usuário se o username for conhecido.
            # **Simplificação:** Se for URL de highlight, vamos tratá-la como um "post" especial.
            # O Instaloader pode conseguir extrair o StoryItem.
            logger.warning("Download de highlight específico por URL direta é experimental.")
            # Tenta carregar o item do highlight como um "Post" (que pode ser um StoryItem)
            # O "shortcode" de um highlight é seu ID.
            try:
                post_obj = instaloader.Post.from_shortcode(L.context, highlight_id) # Destaques são 'posts' especiais
                items_to_download_iter = [post_obj]
            except Exception as e_hl:
                 raise Exception(f"Não foi possível carregar o item do highlight ID {highlight_id}: {e_hl}. Tente baixar todos os highlights do usuário.")

        elif ig_action == 'post' and post_shortcode:
            post = instaloader.Post.from_shortcode(L.context, post_shortcode)
            if post.typename == 'GraphSidecar': # Carrossel
                items_to_download_iter = post.get_sidecar_nodes()
            else: # Post único (imagem ou vídeo)
                items_to_download_iter = [post]
        
        # Se temos um iterador de itens (stories, highlights, carrossel)
        if items_to_download_iter and ig_action != 'profile_pic': # profile_pic já foi baixado
            # O target_profile_name para _download_instagram_items é para a estrutura de pastas.
            # Se for um post, o "profile" é o dono do post. Se for stories, é o username.
            # Vamos usar o profile_username se disponível, ou o dono do primeiro item.
            name_for_subdir = profile_username
            if not name_for_subdir and post_shortcode: # Para posts, pegar o dono
                # Re-obter o post para pegar o owner_username se não tivermos profile_username
                post_for_owner = instaloader.Post.from_shortcode(L.context, post_shortcode)
                name_for_subdir = post_for_owner.owner_username

            if not name_for_subdir: name_for_subdir = "instagram_media" # Fallback

            downloaded_raw_files = _download_instagram_items(L, items_to_download_iter, name_for_subdir, temp_dir)
            downloaded_files.extend(downloaded_raw_files)

        if not downloaded_files:
            raise Exception("Nenhum arquivo do Instagram foi baixado.")

        # Pós-processamento: extrair MP3 se necessário
        final_processed_files = []
        if download_format == 'mp3':
            for media_file_path in downloaded_files:
                if media_file_path.lower().endswith((".mp4", ".mov")):
                    logger.info(f"Instagram: Extraindo áudio MP3 de: {media_file_path}")
                    audio_path = extract_audio_from_video_yt_dlp(media_file_path, os.path.dirname(media_file_path), 'mp3')
                    if audio_path and os.path.exists(audio_path):
                        final_processed_files.append(audio_path)
                        try: # Tenta remover o vídeo original
                            if media_file_path != audio_path : os.remove(media_file_path)
                        except OSError as e_rem: logger.warning(f"Falha ao remover vídeo original {media_file_path}: {e_rem}")
                    else:
                        logger.warning(f"Instagram: Falha ao extrair áudio de {media_file_path}. O arquivo original não será incluído.")
                # Não faz sentido processar imagens para MP3
            if not final_processed_files:
                raise Exception("Nenhum áudio MP3 pôde ser extraído dos vídeos do Instagram baixados.")
        else: # Formato de vídeo/imagem padrão
            final_processed_files.extend(downloaded_files)

        if not final_processed_files:
            raise Exception("Instagram: Nenhum arquivo final processado.")

        # Se múltiplos arquivos, criar ZIP. Senão, retornar o único arquivo.
        if len(final_processed_files) > 1:
            zip_path = create_zip_from_files(final_processed_files, output_filename_base, temp_dir)
            if not zip_path:
                raise Exception("Falha ao criar arquivo ZIP para múltiplos itens do Instagram.")
            # Limpar arquivos individuais após zippar
            for f_path in final_processed_files:
                if os.path.exists(f_path): os.remove(f_path)
            # Limpar o diretório de download do IG se estiver vazio
            ig_dl_main_path = os.path.join(temp_dir, DOWNLOAD_TARGET_DIR_IG)
            if os.path.exists(ig_dl_main_path) and not os.listdir(ig_dl_main_path):
                shutil.rmtree(ig_dl_main_path, ignore_errors=True)
            elif os.path.exists(ig_dl_main_path): # Se ainda houver subpastas de profile
                shutil.rmtree(ig_dl_main_path, ignore_errors=True)


            return [zip_path] # Retorna lista com o caminho do ZIP
        elif final_processed_files:
            return final_processed_files # Lista com um único arquivo
        else:
            raise Exception("Instagram: Nenhum arquivo resultante após processamento.")

    except instaloader.exceptions.ProfileNotExistsException as e:
        raise Exception(f"Instagram: Perfil '{profile_username or url_or_username}' não encontrado: {e}")
    except instaloader.exceptions.ConnectionException as e:
        if "Login required" in str(e) or "Redirected to login page" in str(e) or "401 Unauthorized" in str(e) or "checkpoint_required" in str(e):
            raise Exception(f"Instagram: Acesso negado ou requer login/checkpoint. Tente configurar credenciais/sessão via variáveis de ambiente. Erro: {e}")
        raise Exception(f"Instagram: Falha de conexão: {e}")
    except ValueError as e: # Nossos próprios ValueErrors
        raise e
    except Exception as e:
        logger.exception(f"Erro inesperado no download do Instagram para '{url_or_username}'")
        raise Exception(f"Instagram: Erro inesperado: {e}")


# --- Funções de Download yt-dlp (TikTok, YouTube) ---
def download_with_yt_dlp(platform_name, url, temp_dir, download_format='video', cookie_file_path=None):
    logger.info(f"{platform_name}: URL='{url}', Formato='{download_format}', Cookies={'Sim' if cookie_file_path else 'Não'}")
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s' if platform_name == 'YouTube' else '%(id)s.%(ext)s'),
        'quiet': False, 'verbose': True, 'noplaylist': True,
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'retries': 2, 'fragment_retries': 2,
        'writedescription': False, 'writeinfojson': False, 'writethumbnail': False, 'writeannotations': False,
        'writesubtitles': False, 'writeautomaticsub': False,
        'progress': False, 'noprogress': True, # Desabilitar barra de progresso nos logs
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
    else: # Vídeo
        # H.264 (avc1) + AAC (m4a) em MP4 é o mais compatível
        ydl_opts['format'] = 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4' # Garante container MP4 após o merge

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = None
            
            # Tentar obter o nome do arquivo a partir do info_dict (após download e pós-processamento)
            # O yt-dlp pode ter renomeado o arquivo (ex: para .mp3)
            # O 'filepath' em 'requested_downloads' é geralmente o mais confiável para o arquivo final.
            if 'requested_downloads' in info and info['requested_downloads']:
                 # Pode haver múltiplos em 'requested_downloads' se, por ex., thumbnail também for baixado.
                 # O arquivo de mídia principal é geralmente o primeiro ou o que corresponde à extensão esperada.
                for req_dl_info in info['requested_downloads']:
                    fpath = req_dl_info.get('filepath')
                    if fpath and os.path.exists(fpath):
                        if download_format == 'mp3' and fpath.endswith('.mp3'):
                            downloaded_file = fpath
                            break
                        elif download_format != 'mp3' and (fpath.endswith('.mp4') or fpath.endswith('.mkv') or fpath.endswith('.webm')):
                            downloaded_file = fpath
                            break
                if not downloaded_file and info['requested_downloads']: # Pega o primeiro se nenhum match exato
                    fpath = info['requested_downloads'][0].get('filepath')
                    if fpath and os.path.exists(fpath):
                        downloaded_file = fpath

            # Fallback se 'requested_downloads' não ajudar
            if not downloaded_file or not os.path.exists(downloaded_file):
                # O nome do arquivo pode ter sido alterado pelo postprocessor (ex: para .mp3)
                expected_final_ext = f".{download_format}" if download_format == "mp3" else ".mp4"
                base_name_template = ydl_opts['outtmpl'].replace('.%(ext)s', '') # Tira a extensão do template
                
                # Se o template usa %(title)s, precisamos do título real
                title_or_id = info.get('title', info.get('id', 'downloaded_media'))
                # Sanitizar título para nome de arquivo (simples)
                sane_title_or_id = re.sub(r'[<>:"/\\|?*]', '_', title_or_id)[:100] # Limita comprimento

                # Tentar construir nome esperado
                possible_filename = f"{sane_title_or_id}{expected_final_ext}"
                if os.path.exists(os.path.join(temp_dir, possible_filename)):
                    downloaded_file = os.path.join(temp_dir, possible_filename)
                else: # Listar diretório como último recurso
                    logger.warning(f"{platform_name}: Não foi possível determinar o nome do arquivo do info_dict. Listando '{temp_dir}'.")
                    for f_name in os.listdir(temp_dir):
                        if download_format == 'mp3' and f_name.endswith(".mp3"):
                            downloaded_file = os.path.join(temp_dir, f_name)
                            break
                        elif download_format != 'mp3' and (f_name.endswith(".mp4") or f_name.endswith(".mkv") or f_name.endswith(".webm")):
                            downloaded_file = os.path.join(temp_dir, f_name)
                            break
            
            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception(f"Arquivo final não encontrado em '{temp_dir}'. Conteúdo: {os.listdir(temp_dir)}")

            logger.info(f"{platform_name}: Baixado/Processado com sucesso: {downloaded_file}")
            return [downloaded_file] # Retorna lista com um arquivo

    except DownloadError as e:
        logger.error(f"Erro yt-dlp ({platform_name}) para '{url}': {e}")
        specific_error_msg = f"Falha ao baixar/processar {platform_name}"
        if "Sign in to confirm you're not a bot" in str(e) or "login is required" in str(e):
            specific_error_msg += ": Requer login/cookies (detecção de bot ou restrição)."
        elif "ffmpeg" in str(e).lower() and ("not found" in str(e).lower() or "ailed" in str(e).lower()): # Ex: "ffmpeg failed"
            specific_error_msg += f": Problema com ffmpeg, necessário para formato '{download_format}'."
        elif "Private video" in str(e) or "Video unavailable" in str(e):
            specific_error_msg += ": Vídeo privado ou indisponível."
        elif "Unsupported URL" in str(e):
            specific_error_msg = f"URL não suportada pelo {platform_name} extractor: {url}"
        else:
            specific_error_msg += f": {str(e)}"
        raise Exception(specific_error_msg)
    except Exception as e:
        logger.exception(f"Erro genérico ({platform_name}) para '{url}'")
        raise Exception(f"Falha inesperada no {platform_name}: {e}")

# --- Rota Health Check ---
@app.route('/')
def health_check():
    return "API Media Downloader is healthy and running!", 200

# --- Rota da API Principal ---
@app.route('/api/download', methods=['GET'])
def main_download_route():
    url = request.args.get('url')
    username_ig = request.args.get('username') # Para ações do Instagram baseadas em username
    download_format_req = request.args.get('format', 'video').lower() # video, image, mp3
    ig_action_req = request.args.get('ig_action') # profile_pic, stories, highlights, post (post é inferido de URL)

    if not url and not (username_ig and ig_action_req):
        return jsonify({"error": "Parâmetro 'url' (ou 'username' e 'ig_action' para Instagram) é obrigatório."}), 400
    if download_format_req not in ['video', 'image', 'mp3']:
        return jsonify({"error": "Parâmetro 'format' inválido. Use 'video', 'image' ou 'mp3'."}), 400
    if ig_action_req and ig_action_req not in ['profile_pic', 'stories', 'highlights']:
        return jsonify({"error": "Parâmetro 'ig_action' inválido. Use 'profile_pic', 'stories', 'highlights'."}), 400

    temp_dir_req = tempfile.mkdtemp(prefix="downloader_")
    req_id = os.path.basename(temp_dir_req)
    logger.info(f"Req ID: {req_id} - URL='{url}', UserIG='{username_ig}', Format='{download_format_req}', IGAction='{ig_action_req}'")

    downloaded_file_paths = []
    final_file_to_send = None
    cleanup_paths = [temp_dir_req] # Diretório principal sempre é limpo

    try:
        cookie_file_yt = None
        # Determinar plataforma e executar download
        target_platform_input = url if url else username_ig # O que será passado para a função de download

        if url and ("youtube.com" in url or "youtu.be" in url):
            cookie_file_yt = create_temp_file_from_env('YOUTUBE_COOKIES_FILE_CONTENT', TMP_COOKIE_FILENAME_YT, temp_dir_req)
            if cookie_file_yt: cleanup_paths.append(cookie_file_yt)
            downloaded_file_paths = download_with_yt_dlp('YouTube', url, temp_dir_req, download_format_req, cookie_file_yt)
        elif url and "tiktok.com" in url:
            # TikTok geralmente não precisa de cookies, mas a função suporta
            downloaded_file_paths = download_with_yt_dlp('TikTok', url, temp_dir_req, download_format_req)
        elif (url and "instagram.com" in url) or (username_ig and ig_action_req):
            # Para Instagram, a gestão da sessão é interna em get_instaloader_instance
            instaloader_instance = get_instaloader_instance(temp_dir_req) # Passa temp_dir para gerenciar session file
            downloaded_file_paths = download_instagram_content(instaloader_instance, target_platform_input, temp_dir_req, download_format_req, ig_action_req)
        else:
            return jsonify({"error": "URL não suportada ou combinação de parâmetros inválida."}), 400

        if not downloaded_file_paths:
            raise Exception("Nenhum arquivo foi retornado pela função de download.")

        final_file_to_send = downloaded_file_paths[0] # Geralmente um único arquivo (ou um ZIP)

        if not os.path.exists(final_file_to_send):
            raise Exception(f"Arquivo final '{final_file_to_send}' não encontrado no servidor.")

        mimetype, _ = mimetypes.guess_type(final_file_to_send)
        if final_file_to_send.lower().endswith(".zip"):
            mimetype = "application/zip"
        elif mimetype is None: # Fallbacks
            if final_file_to_send.lower().endswith(".mp4"): mimetype = "video/mp4"
            elif final_file_to_send.lower().endswith(".mp3"): mimetype = "audio/mpeg"
            else: mimetype = 'application/octet-stream'

        logger.info(f"Req ID: {req_id} - Enviando arquivo: {final_file_to_send}, mimetype: {mimetype}")
        
        response = make_response(send_file(
            final_file_to_send,
            as_attachment=True,
            download_name=os.path.basename(final_file_to_send), # Nome do arquivo para o cliente
            mimetype=mimetype
        ))
        # Garantir que o arquivo seja fechado após o envio para permitir a limpeza.
        # send_file com with statement ou um response.call_on_close seria ideal,
        # mas a limpeza no finally é mais simples aqui. O Gunicorn/Flask deve lidar com o fechamento.
        
        return response

    except Exception as e:
        logger.exception(f"Req ID: {req_id} - Erro no processamento da API")
        return jsonify({"error": str(e)}), 500 # Simplificado para retornar a mensagem de erro direta
    finally:
        # Limpeza
        # Se final_file_to_send foi enviado, ele será removido como parte do temp_dir_req
        # Apenas precisamos garantir que outros arquivos temporários (como cookie) sejam removidos se não estiverem no temp_dir_req.
        # No entanto, todos os arquivos temporários criados (cookies, sessões) estão dentro do temp_dir_req.
        logger.info(f"Req ID: {req_id} - Iniciando limpeza de: {temp_dir_req}")
        shutil.rmtree(temp_dir_req, ignore_errors=True)
        logger.info(f"Req ID: {req_id} - Limpeza concluída.")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    # Para desenvolvimento local com Docker:
    # Defina as variáveis de ambiente (YOUTUBE_COOKIES_FILE_CONTENT, INSTAGRAM_USERNAME, etc.)
    # no seu ambiente local ou em um arquivo .env carregado pelo Docker Compose, por exemplo.
    # Ex: docker run -p 8080:8080 -e YOUTUBE_COOKIES_FILE_CONTENT="conteudo..." -e INSTAGRAM_USERNAME="user" ... sua_imagem
    app.run(host='0.0.0.0', port=port, debug=False) # debug=False para produção no Render
