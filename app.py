import os
import logging
import tempfile
import shutil
import mimetypes
from flask import Flask, request, jsonify, send_file, make_response

from utils import create_temp_file_from_env
from instagram_downloader import get_instaloader_instance, download_instagram_content
from platform_downloader import download_with_yt_dlp

# --- Configuração do Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler() # Envia logs para o console (stderr)
    ]
)
logger = logging.getLogger(__name__)

# --- Constantes ---
TMP_COOKIE_FILENAME_YT = "youtube_cookies.txt"

# --- Inicialização do Flask ---
app = Flask(__name__)

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

    try:
        cookie_file_yt = None
        target_platform_input = url if url else username_ig

        if url and ("youtube.com" in url or "youtu.be" in url):
            cookie_file_yt = create_temp_file_from_env('YOUTUBE_COOKIES_FILE_CONTENT', TMP_COOKIE_FILENAME_YT, temp_dir_req)
            downloaded_file_paths = download_with_yt_dlp('YouTube', url, temp_dir_req, download_format_req, cookie_file_yt)
        elif url and "tiktok.com" in url:
            downloaded_file_paths = download_with_yt_dlp('TikTok', url, temp_dir_req, download_format_req)
        elif (url and "instagram.com" in url) or (username_ig and ig_action_req):
            instaloader_instance = get_instaloader_instance(temp_dir_req)
            downloaded_file_paths = download_instagram_content(instaloader_instance, target_platform_input, temp_dir_req, download_format_req, ig_action_req)
        else:
            return jsonify({"error": "URL não suportada ou combinação de parâmetros inválida."}), 400

        if not downloaded_file_paths:
            raise Exception("Nenhum arquivo foi retornado pela função de download.")

        final_file_to_send = downloaded_file_paths[0]

        if not os.path.exists(final_file_to_send):
            raise Exception(f"Arquivo final '{final_file_to_send}' não encontrado no servidor.")

        mimetype, _ = mimetypes.guess_type(final_file_to_send)
        if final_file_to_send.lower().endswith(".zip"):
            mimetype = "application/zip"
        elif mimetype is None:
            if final_file_to_send.lower().endswith(".mp4"): mimetype = "video/mp4"
            elif final_file_to_send.lower().endswith(".mp3"): mimetype = "audio/mpeg"
            else: mimetype = 'application/octet-stream'

        logger.info(f"Req ID: {req_id} - Enviando arquivo: {final_file_to_send}, mimetype: {mimetype}")
        
        response = make_response(send_file(
            final_file_to_send,
            as_attachment=True,
            download_name=os.path.basename(final_file_to_send),
            mimetype=mimetype
        ))
        
        return response

    except Exception as e:
        logger.exception(f"Req ID: {req_id} - Erro no processamento da API")
        return jsonify({"error": str(e)}), 500
    finally:
        logger.info(f"Req ID: {req_id} - Iniciando limpeza de: {temp_dir_req}")
        shutil.rmtree(temp_dir_req, ignore_errors=True)
        logger.info(f"Req ID: {req_id} - Limpeza concluída.")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
