import os
import logging
import shutil
import instaloader
import base64
import re
from utils import extract_audio_from_video_if_needed, create_zip_from_files

logger = logging.getLogger(__name__)

# --- Constantes Específicas do Instagram ---
DOWNLOAD_TARGET_DIR_IG = "ig_media"
TMP_SESSION_FILENAME_IG = "instagram_session.tmp"

def get_instaloader_instance(temp_dir):
    """Cria e configura uma instância do Instaloader, gerenciando a sessão."""
    session_file_path = os.path.join(temp_dir, TMP_SESSION_FILENAME_IG)
    L = instaloader.Instaloader(
        download_pictures=True, download_videos=True, download_video_thumbnails=False,
        save_metadata=False, compress_json=False,
        dirname_pattern=os.path.join(temp_dir, DOWNLOAD_TARGET_DIR_IG, "{profile}"),
        filename_pattern="{date_utc}__{mediaid}",
        quiet=False, # Silenciar a saída do instaloader?
    )

    # Tenta carregar a sessão a partir da variável de ambiente
    session_content_b64 = os.environ.get('INSTAGRAM_SESSION_FILE_CONTENT')
    if session_content_b64:
        try:
            session_content = base64.b64decode(session_content_b64).decode('utf-8')
            with open(session_file_path, 'w') as f:
                f.write(session_content)
            L.load_session_from_file(os.environ['INSTAGRAM_USERNAME'], session_file_path)
            logger.info("Instaloader: Sessão carregada com sucesso a partir do arquivo de sessão.")
            return L
        except Exception as e:
            logger.error(f"Instaloader: Falha ao carregar sessão a partir do arquivo: {e}")
            # Continua para tentar login com user/pass se o carregamento falhar

    # Fallback para login com usuário e senha se o carregamento da sessão falhar ou não existir
    username = os.environ.get('INSTAGRAM_USERNAME')
    password = os.environ.get('INSTAGRAM_PASSWORD')
    if username and password:
        try:
            L.login(username, password)
            logger.info(f"Instaloader: Login bem-sucedido para o usuário '{username}'.")
            # Salva a nova sessão para uso futuro (opcional, mas bom para depuração)
            L.save_session_to_file(session_file_path)
            logger.info(f"Instaloader: Nova sessão salva em '{session_file_path}'.")
            return L
        except Exception as e:
            logger.error(f"Instaloader: Falha no login para o usuário '{username}': {e}")
            raise Exception("Falha na autenticação com Instagram. Verifique as credenciais.")
    
    logger.warning("Instaloader: Nenhuma sessão ou credenciais fornecidas. A funcionalidade será limitada.")
    return L

def download_instagram_content(L, url_or_username, temp_dir_req, download_format, ig_action=None):
    """Baixa conteúdo do Instagram (posts, stories, etc.) usando uma instância do Instaloader."""
    profile_username = None
    is_single_post = False
    
    if ig_action in ['stories', 'highlights', 'profile_pic']:
        profile_username = url_or_username
    elif "/stories/" in url_or_username or "/s/" in url_or_username:
        ig_action = 'stories' # Inferir stories de URL
        match = re.search(r'/(?:stories|s)/([a-zA-Z0-9_.-]+)', url_or_username)
        if not match:
            raise ValueError("URL de Story do Instagram inválida.")
        profile_username = match.group(1)
    else: # É um post ou perfil
        is_single_post = "/p/" in url_or_username or "/reel/" in url_or_username
        if is_single_post:
            ig_action = 'post'
        else: # Se não for post, asumimos que é um perfil para baixar tudo
            ig_action = 'profile' 
            profile_username = url_or_username

    try:
        if ig_action == 'post':
            match = re.search(r"/(?:p|reel)/([A-Za-z0-9-_]+)", url_or_username)
            if not match:
                raise ValueError("URL de Post do Instagram inválida.")
            shortcode = match.group(1)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG, post.owner_username))
        elif profile_username:
            profile = instaloader.Profile.from_username(L.context, profile_username)
            if ig_action == 'profile_pic':
                L.download_profilepic(profile, profile_pic_only=True)
            elif ig_action == 'stories':
                L.download_stories(userids=[profile.userid], filename_target=os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG, profile_username))
            elif ig_action == 'highlights':
                L.download_highlights(userids=[profile.userid], filename_target=os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG, profile_username))
            elif ig_action == 'profile': # Baixar todos os posts do perfil
                L.download_profile(profile, profile_pic_only=False)
        else:
            raise ValueError("Entrada para download do Instagram não reconhecida.")

        # --- Pós-processamento e Coleta de Arquivos ---
        absolute_download_path_for_L = os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG, profile_username or post.owner_username)
        logger.info(f"Procurando por mídias baixadas em: {absolute_download_path_for_L}")

        if not os.path.exists(absolute_download_path_for_L):
            raise Exception(f"Diretório de download do Instagram não encontrado: {absolute_download_path_for_L}")

        downloaded_videos = []
        downloaded_images = []
        for f in os.listdir(absolute_download_path_for_L):
            if f.endswith('.mp4'):
                downloaded_videos.append(os.path.join(absolute_download_path_for_L, f))
            elif f.endswith('.jpg') or f.endswith('.png'):
                downloaded_images.append(os.path.join(absolute_download_path_for_L, f))

        if not downloaded_videos and not downloaded_images:
            raise Exception("Nenhum arquivo do Instagram resultante foi encontrado após o download.")

        processed_files_for_output = []
        if download_format == 'mp3':
            if not downloaded_videos:
                raise ValueError("Formato 'mp3' solicitado, mas nenhum vídeo foi encontrado para converter.")
            processed_files_for_output = extract_audio_from_video_if_needed(downloaded_videos, temp_dir_req)
            # Limpar vídeos originais após conversão
            for v_path in downloaded_videos:
                if os.path.exists(v_path): os.remove(v_path)
        elif download_format == 'video':
            processed_files_for_output = downloaded_videos
        elif download_format == 'image':
            processed_files_for_output = downloaded_images

        if not processed_files_for_output:
            raise Exception(f"Nenhum arquivo correspondente ao formato '{download_format}' foi encontrado.")

        # --- Empacotamento e Retorno ---
        if len(processed_files_for_output) > 1:
            zip_filename = f"instagram_{profile_username or post.owner_username}_{ig_action}.zip"
            zip_path = create_zip_from_files(processed_files_for_output, zip_filename, temp_dir_req)
            if not zip_path:
                raise Exception("Falha ao criar arquivo ZIP.")
            
            # Limpar arquivos individuais após zippar (eles estão em temp_dir_req ou absolute_download_path_for_L)
            for f_path in processed_files_for_output:
                if os.path.exists(f_path): os.remove(f_path)
            # Remover o diretório de download do Instaloader (que está dentro de temp_dir_req)
            ig_media_root_in_temp = os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG)
            if os.path.exists(ig_media_root_in_temp):
                shutil.rmtree(ig_media_root_in_temp, ignore_errors=True)
            return [zip_path]
        elif processed_files_for_output:
            # Se for um único arquivo, ele pode estar em temp_dir_req (se for mp3 convertido)
            # ou em absolute_download_path_for_L (se for o original).
            # Para consistência, vamos mover para temp_dir_req se não estiver lá.
            single_file = processed_files_for_output[0]
            if os.path.dirname(single_file) != temp_dir_req:
                moved_single_file = os.path.join(temp_dir_req, os.path.basename(single_file))
                shutil.move(single_file, moved_single_file)
                # Limpar o diretório de download do Instaloader
                ig_media_root_in_temp = os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG)
                if os.path.exists(ig_media_root_in_temp):
                    shutil.rmtree(ig_media_root_in_temp, ignore_errors=True)
                return [moved_single_file]
            return [single_file]
        else: # Esta branch não deveria ser atingida se a exceção anterior foi levantada.
            raise Exception("Instagram: Nenhum arquivo final para retornar.")

    except instaloader.exceptions.ProfileNotExistsException as e:
        raise Exception(f"Instagram: Perfil '{profile_username or url_or_username}' não encontrado: {e}")
    except ValueError as e: # Nossos próprios ValueErrors
        raise e
    except Exception as e:
        logger.exception(f"Erro inesperado no download do Instagram para '{url_or_username}'")
        if "Nenhum arquivo do Instagram resultante" in str(e) or "Nenhum vídeo encontrado para converter" in str(e):
             raise e # Re-levanta a exceção mais específica
        raise Exception(f"Instagram: Erro inesperado durante o processamento: {e}")