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

# Função get_instaloader_instance modificada para usar Base64

import base64 # Adicionar no topo do app.py

def get_instaloader_instance(temp_dir_for_session_management): # temp_dir_for_session_management não será mais usado para dirname_pattern aqui
    L = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        storyitem_metadata_txt_pattern="",
        # Removido dirname_pattern daqui ou simplificado:
        # dirname_pattern="{profile}" # Ou deixe o padrão do Instaloader (CWD/{profile})
    )

    
    ig_session_base64 = os.environ.get('INSTAGRAM_SESSION_BASE64')
    ig_username_for_session = os.environ.get('INSTAGRAM_USERNAME')
    session_loaded_successfully = False

    # Prioridade 1: Carregar sessão de Base64
    if ig_session_base64 and ig_username_for_session:
        session_filepath = os.path.join(temp_dir_for_session_management, f"{ig_username_for_session}.session")
        try:
            logger.info(f"Instagram: Decodificando sessão Base64 para '{ig_username_for_session}'.")
            session_bytes = base64.b64decode(ig_session_base64.encode('ascii'))
            
            # Escrever os bytes decodificados diretamente no arquivo em modo binário
            with open(session_filepath, 'wb') as f_write_bytes:
                f_write_bytes.write(session_bytes)
            
            logger.info(f"Instagram: Tentando carregar sessão (de Base64) para '{ig_username_for_session}' do arquivo '{session_filepath}'.")
            L.context.username = ig_username_for_session
            
            with open(session_filepath, 'rb') as session_file_object:
                L.context.load_session_from_file(ig_username_for_session, session_file_object)

            if L.context.is_logged_in and L.context.username == ig_username_for_session:
                logger.info(f"Instagram: Sessão (de Base64) carregada com SUCESSO para '{L.context.username}'.")
                session_loaded_successfully = True
                return L
            else:
                logger.warning(f"Instagram: Sessão (de Base64) carregada mas contexto indica erro (logged_in: {L.context.is_logged_in}, user: {L.context.username}).")
                if os.path.exists(session_filepath): os.remove(session_filepath)
        
        except Exception as e:
            logger.warning(f"Instagram: Falha ao processar/carregar sessão Base64 para '{ig_username_for_session}' (Exceção: {type(e).__name__} - {e}).")
            if os.path.exists(session_filepath): os.remove(session_filepath)
    
    # (Opcional: Fallback para INSTAGRAM_SESSION_FILE_CONTENT se a Base64 falhar ou não existir)
    # Se você quiser manter o fallback, pode adicionar o código anterior aqui,
    # mas para simplificar o teste, vamos focar no Base64 primeiro.
    # if not session_loaded_successfully:
    #    ig_session_content = os.environ.get('INSTAGRAM_SESSION_FILE_CONTENT')
    #    if ig_session_content and ig_username_for_session:
    #        # ... (lógica anterior com open 'w', encoding='utf-8' e depois open 'rb') ...
    #        pass # Implementar se desejar o fallback

    # Prioridade 2: Login com username/password (se sessão falhou)
    if not session_loaded_successfully:
        username_login = os.environ.get('INSTAGRAM_USERNAME')
        password_login = os.environ.get('INSTAGRAM_PASSWORD')

        if username_login and password_login:
            logger.info(f"Instagram: Tentando login com username/password para '{username_login}'.")
            try:
                L.login(username_login, password_login)
                logger.info(f"Instagram: Login via username/password bem-sucedido como '{L.context.username}'.")
                session_loaded_successfully = True # Marcar como sucesso para o log final
                return L
            except Exception as e:
                logger.warning(f"Instagram: Login via username/password falhou para '{username_login}': {e}.")
        else:
            if not (ig_session_base64 and ig_username_for_session): # Só loga isso se não tentou sessão
                 logger.info("Instagram: Nenhuma sessão (Base64 ou direta) ou login/password fornecido.")
    
    final_status_msg = f"Instagram: Instaloader instanciado. Status do login: {L.context.is_logged_in}"
    if L.context.is_logged_in:
        final_status_msg += f", Username: {L.context.username}"
    else:
        final_status_msg += ", Username: Anônimo"
    logger.info(final_status_msg)
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


def download_instagram_content(L, url_or_username, temp_dir_req, download_format='video', ig_action=None):
    """Função principal para lidar com downloads do Instagram."""
    processed_files_for_output = []
    output_filename_base = "instagram_media"
    
    # Diretório onde o Instaloader vai salvar os arquivos desta requisição específica
    # Ex: /tmp/downloader_xyz/instagram_downloads/juuh._.038
    # O nome da subpasta (target_profile_for_subdir) será determinado abaixo.
    target_profile_for_subdir = None 

    try:
        profile_username = None
        post_shortcode = None
        
        # Determinar alvo e ação (como antes)
        if ig_action:
            profile_username = url_or_username
            target_profile_for_subdir = profile_username
            output_filename_base = f"{profile_username}_{ig_action}"
        else: # Inferir da URL
            # ... (sua lógica de regex para extrair profile_username, post_shortcode, story_id, highlight_id)
            # ... Vou assumir que esta parte preenche profile_username ou post_shortcode
            # ... e define ig_action e output_filename_base corretamente.
            # Exemplo simplificado:
            post_match = re.search(r"instagram\.com/(?:p|reel|tv)/([^/]+)", url_or_username)
            story_url_match = re.search(r"instagram\.com/stories/([^/]+)/(\d+)", url_or_username)
            profile_url_match = re.search(r"instagram\.com/([^/]+)", url_or_username) # Genérico

            if post_match:
                post_shortcode = post_match.group(1)
                ig_action = 'post' # ou reel, igtv
                # Precisamos do owner para o nome do subdiretório
                temp_post_for_owner = instaloader.Post.from_shortcode(L.context, post_shortcode)
                target_profile_for_subdir = temp_post_for_owner.owner_username
                output_filename_base = f"{target_profile_for_subdir}_post_{post_shortcode}"
            elif story_url_match:
                profile_username = story_url_match.group(1)
                # story_id = story_url_match.group(2) # Não usado diretamente no download de todos os stories
                ig_action = 'stories' # Ou um 'story_item' se você implementar
                target_profile_for_subdir = profile_username
                output_filename_base = f"{profile_username}_stories"
            elif profile_url_match and ig_action: # Ex: username=user&ig_action=stories
                 profile_username = profile_url_match.group(1)
                 target_profile_for_subdir = profile_username
                 output_filename_base = f"{profile_username}_{ig_action}"
            elif profile_url_match and not ig_action: # URL de perfil sem ação específica
                 raise ValueError("Para URL de perfil do Instagram, por favor, especifique 'ig_action' (profile_pic, stories, highlights).")
            else: # Se url_or_username é apenas um username (sem URL) e ig_action está setado
                 if not ig_action:
                     raise ValueError("Nome de usuário fornecido sem 'ig_action' ou URL inválida.")
                 profile_username = url_or_username # url_or_username é o username
                 target_profile_for_subdir = profile_username
                 output_filename_base = f"{profile_username}_{ig_action}"


        if not target_profile_for_subdir:
            raise ValueError("Não foi possível determinar o alvo para o subdiretório de download do Instagram.")

        # Configurar o caminho de download absoluto para o Instaloader
        absolute_download_path_for_L = os.path.join(temp_dir_req, DOWNLOAD_TARGET_DIR_IG, target_profile_for_subdir)
        os.makedirs(absolute_download_path_for_L, exist_ok=True)
        L.dirname_pattern = absolute_download_path_for_L
        logger.info(f"Instagram: Configurado para baixar em '{absolute_download_path_for_L}'")

        # Obter o objeto Profile se tivermos profile_username
        profile = None
        if profile_username:
            try:
                profile = instaloader.Profile.from_username(L.context, profile_username)
            except instaloader.exceptions.ProfileNotExistsException:
                raise Exception(f"Perfil do Instagram '{profile_username}' não encontrado.")

        # --- Executar a Ação de Download REAL ---
        if ig_action == 'profile_pic' and profile:
            L.download_profilepic(profile) # Baixa para dirname_pattern / {profile_pic_filename}
            logger.info(f"Baixando foto de perfil para {profile.username}")
        elif ig_action == 'stories' and profile:
            logger.info(f"Baixando stories para {profile.username}")
            for story in L.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    L.download_storyitem(item, target=profile.username) # target aqui é para o {profile} no filename_pattern
        elif ig_action == 'highlights' and profile:
            logger.info(f"Baixando destaques para {profile.username}")
            for highlight in L.get_highlights(user=profile):
                logger.info(f"  Baixando itens do destaque: {highlight.title}")
                for item in highlight.get_items():
                    L.download_storyitem(item, target=profile.username)
        elif ig_action == 'post' and post_shortcode:
            logger.info(f"Baixando post/reel/IGTV: {post_shortcode}")
            post = instaloader.Post.from_shortcode(L.context, post_shortcode)
            L.download_post(post, target=post.owner_username) # Passa o dono para o filename_pattern
        else:
            raise ValueError(f"Ação do Instagram '{ig_action}' não suportada ou perfil/post não encontrado.")

        # --- Coletar Arquivos Baixados ---
        # Os arquivos estarão em `absolute_download_path_for_L`
        downloaded_raw_files = []
        if os.path.exists(absolute_download_path_for_L):
            for f_name in os.listdir(absolute_download_path_for_L):
                if f_name.endswith((".jpg", ".jpeg", ".png", ".mp4", ".mov")):
                    downloaded_raw_files.append(os.path.join(absolute_download_path_for_L, f_name))
        
        if not downloaded_raw_files:
            # Esta verificação agora é mais precisa. Se nada foi baixado AQUI, então é um problema.
            # Pode ser que o usuário não tenha stories, ou a conta seja privada e não seguida, etc.
            # É importante distinguir entre "erro de código" e "sem conteúdo para baixar".
            logger.warning(f"Instagram: Nenhum arquivo encontrado em '{absolute_download_path_for_L}' após tentativa de download para '{target_profile_for_subdir}', ação '{ig_action}'. Verifique se há conteúdo disponível e se a conta logada tem permissão.")
            # Não levantar exceção aqui ainda, pode ser que não haja conteúdo.
            # A exceção "Nenhum arquivo do Instagram foi baixado." será levantada depois se a lista final estiver vazia.

        # --- Pós-processamento: extrair MP3 se necessário ---
        processed_files_for_output = []
        if download_format == 'mp3':
            if not downloaded_raw_files: # Se não baixou nada, não tem o que converter
                 raise Exception(f"Nenhum vídeo encontrado para converter para MP3 para '{target_profile_for_subdir}'.")
            for media_file_path in downloaded_raw_files:
                if media_file_path.lower().endswith((".mp4", ".mov")):
                    logger.info(f"Instagram: Extraindo áudio MP3 de: {media_file_path}")
                    # O diretório de saída para o MP3 deve ser o mesmo `absolute_download_path_for_L`
                    # ou diretamente no `temp_dir_req` para simplificar a coleta para o ZIP.
                    # Vamos colocar no `temp_dir_req` para evitar aninhamento excessivo no ZIP.
                    audio_path = extract_audio_from_video_yt_dlp(media_file_path, temp_dir_req, 'mp3')
                    if audio_path and os.path.exists(audio_path):
                        processed_files_for_output.append(audio_path)
                        try:
                            if media_file_path != audio_path : os.remove(media_file_path)
                        except OSError as e_rem: logger.warning(f"Falha ao remover vídeo original {media_file_path}: {e_rem}")
                    else:
                        logger.warning(f"Instagram: Falha ao extrair áudio de {media_file_path}.")
            if not processed_files_for_output:
                raise Exception("Nenhum áudio MP3 pôde ser extraído dos vídeos do Instagram.")
        else: # Formato de vídeo/imagem padrão
            processed_files_for_output.extend(downloaded_raw_files)

        if not processed_files_for_output:
            # Esta exceção agora é mais significativa, pois significa que ou não havia conteúdo,
            # ou a conversão para MP3 falhou para todos os itens, ou a filtragem falhou.
            raise Exception("Nenhum arquivo do Instagram resultante após processamento (verifique se há conteúdo ou se a conversão para MP3 foi bem-sucedida).")

        # --- Criar ZIP se múltiplos arquivos ---
        if len(processed_files_for_output) > 1:
            zip_path = create_zip_from_files(processed_files_for_output, output_filename_base, temp_dir_req)
            if not zip_path:
                raise Exception("Falha ao criar arquivo ZIP para múltiplos itens do Instagram.")
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
    # ... (resto dos seus blocos except como antes) ...
    except ValueError as e: # Nossos próprios ValueErrors
        raise e
    except Exception as e:
        logger.exception(f"Erro inesperado no download do Instagram para '{url_or_username}'")
        # A exceção original (e) pode ser a de "Nenhum arquivo..."
        # Precisamos ter cuidado para não mascarar a causa raiz.
        if "Nenhum arquivo do Instagram resultante" in str(e) or "Nenhum vídeo encontrado para converter" in str(e):
             raise e # Re-levanta a exceção mais específica
        raise Exception(f"Instagram: Erro inesperado durante o processamento: {e}")


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
