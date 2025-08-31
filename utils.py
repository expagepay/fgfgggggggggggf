import os
import logging
import subprocess
import zipfile
import base64

logger = logging.getLogger(__name__)

def create_temp_file_from_env(env_var_name, temp_file_name, temp_dir):
    """Cria um arquivo temporário a partir de uma variável de ambiente codificada em Base64."""
    env_content_b64 = os.environ.get(env_var_name)
    if not env_content_b64:
        logger.warning(f"Variável de ambiente '{env_var_name}' não encontrada.")
        return None
    try:
        file_content = base64.b64decode(env_content_b64).decode('utf-8')
        temp_file_path = os.path.join(temp_dir, temp_file_name)
        with open(temp_file_path, 'w') as f:
            f.write(file_content)
        logger.info(f"Arquivo temporário '{temp_file_path}' criado a partir de '{env_var_name}'.")
        return temp_file_path
    except Exception as e:
        logger.error(f"Falha ao criar arquivo temporário a partir de '{env_var_name}': {e}")
        return None

def extract_audio_from_video_if_needed(video_files, temp_dir):
    """Extrai áudio MP3 de arquivos de vídeo usando FFmpeg."""
    audio_files = []
    if not video_files:
        return audio_files

    for video_path in video_files:
        if not os.path.exists(video_path):
            logger.warning(f"Arquivo de vídeo não encontrado para extração de áudio: {video_path}")
            continue
        
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        output_audio_path = os.path.join(temp_dir, f"{base_name}.mp3")
        
        command = [
            'ffmpeg',
            '-i', video_path,
            '-q:a', '0',
            '-map', 'a',
            '-y',
            output_audio_path
        ]
        
        logger.info(f"Tentando extrair áudio de '{video_path}' para '{output_audio_path}'")
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            logger.info(f"Áudio extraído com sucesso: {output_audio_path}")
            audio_files.append(output_audio_path)
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg falhou ao extrair áudio de '{video_path}'.")
            logger.error(f"Comando: {' '.join(command)}")
            logger.error(f"Stderr: {e.stderr}")
        except FileNotFoundError:
            logger.error("Comando 'ffmpeg' não encontrado. Certifique-se de que o FFmpeg está instalado e no PATH do sistema.")
            raise Exception("Dependência 'ffmpeg' não encontrada.")

    if not audio_files:
        logger.warning("Nenhum arquivo de áudio foi extraído dos vídeos fornecidos.")

    return audio_files

def create_zip_from_files(files_to_zip, zip_filename, target_dir):
    """Cria um arquivo ZIP a partir de uma lista de caminhos de arquivo."""
    if not files_to_zip:
        return None
    
    zip_path = os.path.join(target_dir, zip_filename)
    logger.info(f"Criando ZIP '{zip_path}' com {len(files_to_zip)} arquivo(s).")
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in files_to_zip:
            if os.path.exists(file_path):
                zipf.write(file_path, os.path.basename(file_path))
                logger.debug(f"Adicionado ao ZIP: {os.path.basename(file_path)}")
            else:
                logger.warning(f"Arquivo não encontrado para adicionar ao ZIP: {file_path}")
                
    return zip_path