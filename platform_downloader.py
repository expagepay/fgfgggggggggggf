import os
import logging
import re
import yt_dlp
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)

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
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6792.57 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                    'image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Referer': 'https://www.youtube.com/',
        }
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
            
            if 'requested_downloads' in info and info['requested_downloads']:
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

            if not downloaded_file or not os.path.exists(downloaded_file):
                expected_final_ext = f".{download_format}" if download_format == "mp3" else ".mp4"
                base_name_template = ydl_opts['outtmpl'].replace('.%(ext)s', '')
                
                title_or_id = info.get('title', info.get('id', 'downloaded_media'))
                sane_title_or_id = re.sub(r'[<>:"/\\|?*]', '_', title_or_id)[:100]

                possible_filename = f"{sane_title_or_id}{expected_final_ext}"
                if os.path.exists(os.path.join(temp_dir, possible_filename)):
                    downloaded_file = os.path.join(temp_dir, possible_filename)
                else: 
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
            return [downloaded_file]

    except DownloadError as e:
        logger.error(f"Erro yt-dlp ({platform_name}) para '{url}': {e}")
        specific_error_msg = f"Falha ao baixar/processar {platform_name}"
        if "Sign in to confirm you're not a bot" in str(e) or "login is required" in str(e):
            specific_error_msg += ": Requer login/cookies (detecção de bot ou restrição)."
        elif "ffmpeg" in str(e).lower() and ("not found" in str(e).lower() or "ailed" in str(e).lower()):
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