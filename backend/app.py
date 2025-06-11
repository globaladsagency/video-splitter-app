import os
import shutil
import json
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS, cross_origin
from moviepy.editor import VideoFileClip
from datetime import datetime
import threading
import time

# --- Celery Configuration ---
from celery import Celery

# Configuración de Celery
# Asegúrate de que Redis está corriendo en el servidor (localhost:6379 es el default)
# Puedes usar variables de entorno para mayor seguridad en producción
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')

celery_app = Celery('video_splitter_tasks', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)
celery_app.conf.update(
    result_expires=3600, # Los resultados de las tareas expiran después de 1 hora
    task_acks_late=True, # Solo acusa recibo de la tarea cuando se completa
    task_reject_on_worker_timeout=True # Rechaza la tarea si el worker timeout
)

# --- Flask App Configuration ---
app = Flask(__name__)
# Configuración CORS para permitir solicitudes desde el frontend (ajusta si es necesario)
cors = CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'

# Rutas para guardar archivos subidos y fragmentos
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
FRAGMENT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fragments')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['FRAGMENT_FOLDER'] = FRAGMENT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024 # Límite de 500 MB para subidas (ajusta según necesites)

# Crea las carpetas si no existen
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(FRAGMENT_FOLDER, exist_ok=True)


# --- Helper Function for Video Splitting Logic (now a Celery task) ---
# Esta es la función que realmente hace el trabajo pesado
@celery_app.task(bind=True) # bind=True permite acceder al objeto de la tarea (self)
def process_video_task(self, video_path, chunk_duration, session_id):
    """
    Función que realiza la división de video.
    Se ejecuta como una tarea de Celery en segundo plano.
    """
    current_session_fragment_dir = os.path.join(FRAGMENT_FOLDER, session_id)
    try:
        # Asegúrate de que los directorios de salida existen
        os.makedirs(current_session_fragment_dir, exist_ok=True)

        clip = VideoFileClip(video_path)
        total_duration = clip.duration
        
        # Opcional: Actualizar el estado de la tarea para seguimiento de progreso
        self.update_state(state='PROGRESS', meta={'status': 'Starting video processing', 'session_id': session_id})

        fragments_info = []
        for i, start_time in enumerate(range(0, int(total_duration), chunk_duration)):
            end_time = min(start_time + chunk_duration, total_duration)
            if start_time >= end_time:
                break

            fragment_name = f"fragment_{i + 1}.mp4"
            output_path = os.path.join(current_session_fragment_dir, fragment_name)

            subclip = clip.subclip(start_time, end_time)
            subclip.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=clip.fps)
            
            # Construye la URL pública para el fragmento
            # Esto asume que tu Nginx/Flask sirve /fragments/<session_id>/<filename>
            public_url = f"/fragments/{session_id}/{fragment_name}"
            fragments_info.append({"name": fragment_name, "url": public_url}) # Cambiado a 'url'

            # Opcional: Actualizar el estado de la tarea con progreso
            progress_percent = (i + 1) / (total_duration / chunk_duration) * 100
            self.update_state(state='PROGRESS', meta={
                'status': f'Processing fragment {i+1} of {int(total_duration / chunk_duration) + 1}',
                'progress': f'{progress_percent:.2f}%',
                'session_id': session_id
            })

        clip.close() # Cierra el clip para liberar recursos

        # Limpiar el archivo subido original después de procesar
        if os.path.exists(video_path):
            os.remove(video_path)

        # Retorna el resultado final que Celery almacenará y el frontend recuperará
        return {"status": "success", "message": "Video processed successfully", "fragments": fragments_info, "session_id": session_id}

    except Exception as e:
        # Limpiar el directorio de fragmentos de la sesión actual si hay un error
        if os.path.exists(current_session_fragment_dir):
            shutil.rmtree(current_session_fragment_dir)
        
        # Registrar el error completo para depuración
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error during video processing task for session {session_id}: {e}\n{error_trace}")
        
        # Actualizar el estado de la tarea a FAILURE y devolver el error
        self.update_state(state='FAILURE', meta={'status': 'Processing failed', 'error': str(e), 'trace': error_trace, 'session_id': session_id})
        return {"status": "error", "message": str(e), "session_id": session_id, "traceback": error_trace}


# --- Flask Routes ---

@app.route('/')
@cross_origin()
def index():
    # Esto le dice a Flask que cargue tu index.html desde la carpeta 'templates'
    return render_template('index.html')


@app.route('/api/split_video', methods=['POST'])
@cross_origin()
def split_video_endpoint():
    if 'video' not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    video_file = request.files['video']
    chunk_duration_str = request.form.get('chunkDuration', '60') # Default 60 segundos

    if video_file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    session_id = datetime.now().strftime("%Y%m%d%H%M%S%f") # Generar ID de sesión único (con microsegundos para mayor unicidad)
    session_upload_dir = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_upload_dir, exist_ok=True)
    
    # Guarda el archivo temporalmente antes de pasarlo a la tarea
    video_filename = video_file.filename
    video_path = os.path.join(session_upload_dir, video_filename)
    video_file.save(video_path)

    try:
        chunk_duration = int(chunk_duration_str)
        if chunk_duration <= 0:
            return jsonify({"error": "Chunk duration must be a positive integer"}), 400
    except ValueError:
        return jsonify({"error": "Invalid chunk duration. Must be an integer."}), 400

    # Enviar la tarea de procesamiento a Celery
    task = process_video_task.delay(video_path, chunk_duration, session_id)
    
    # Devolver el ID de la tarea inmediatamente al cliente
    return jsonify({
        "message": "Video processing started successfully",
        "task_id": task.id,
        "session_id": session_id, # Enviamos el session_id desde el inicio
        "status_url": f"/api/task_status/{task.id}" # URL para chequear estado
    }), 202 # Código 202 Accepted significa que la solicitud fue aceptada para procesamiento

    # En caso de error inesperado al guardar o llamar a Celery
    return jsonify({"error": "An unexpected error occurred."}), 500


@app.route('/api/task_status/<task_id>', methods=['GET'])
@cross_origin()
def get_task_status(task_id):
    """
    Ruta para que el frontend pueda consultar el estado de una tarea de Celery.
    """
    task = celery_app.AsyncResult(task_id)
    
    if task.state == 'PENDING':
        # Para PENDING, Celery solo tiene la información básica.
        response = {
            'state': task.state,
            'status': 'Task is pending or not started.',
            'session_id': task.info.get('session_id') if isinstance(task.info, dict) else None # Intentar obtenerlo si ya está en info
        }
    elif task.state == 'PROGRESS':
        # task.info contiene los metadatos de progreso actualizados por self.update_state
        response = {
            'state': task.state,
            'status': task.info.get('status', 'Processing...'),
            'progress': task.info.get('progress', '0%'),
            'session_id': task.info.get('session_id') # session_id se envía en el meta de PROGRESS
        }
    elif task.state == 'SUCCESS':
        # task.result es el valor retornado por la función de la tarea (process_video_task)
        result_data = task.result # Esto es el diccionario devuelto por process_video_task
        response = {
            'state': task.state,
            'status': result_data.get('message', 'Task completed!'),
            'fragments': result_data.get('fragments', []),
            'session_id': result_data.get('session_id') # Asegura que el session_id se obtiene del resultado final
        }
    elif task.state == 'FAILURE':
        # task.info contiene los metadatos del error
        response = {
            'state': task.state,
            'status': task.info.get('status', 'Task failed!'),
            'error': task.info.get('error', 'Unknown error'),
            'traceback': task.info.get('traceback', 'No traceback available'),
            'session_id': task.info.get('session_id')
        }
    else: # Si el estado es desconocido o REVOKED, RETRY, etc.
        response = {
            'state': task.state,
            'status': 'Unknown task state or task revoked/retrying.',
            'info': task.info # Incluir toda la info para depuración
        }
    return jsonify(response)


@app.route('/fragments/<session_id>/<path:filename>')
@cross_origin()
def download_fragment(session_id, filename):
    safe_path = os.path.join(FRAGMENT_FOLDER, session_id)
    # Seguridad básica: Asegúrate de que el filename no intente acceder a rutas superiores
    if ".." in filename or filename.startswith('/'):
        return jsonify({"error": "Invalid filename"}), 400
    
    full_file_path = os.path.join(safe_path, filename)
    
    if not os.path.exists(full_file_path):
        return jsonify({"error": "Session or fragment not found"}), 404
    
    try:
        # send_from_directory automáticamente maneja los encabezados para descarga
        return send_from_directory(safe_path, filename, as_attachment=False) # as_attachment=False para permitir previsualización
    except Exception as e:
        print(f"Error serving fragment {filename} for session {session_id}: {e}")
        return jsonify({"error": "Could not serve file."}), 500


# Ruta para limpiar archivos de una sesión específica
@app.route('/api/cleanup/<session_id>', methods=['POST'])
@cross_origin()
def cleanup_session(session_id):
    # Seguridad básica: Asegúrate de que el session_id es un ID de sesión válido
    if not session_id or ".." in session_id or "/" in session_id:
        return jsonify({"message": "Invalid session ID."}), 400

    session_upload_path = os.path.join(UPLOAD_FOLDER, session_id)
    session_fragment_path = os.path.join(FRAGMENT_FOLDER, session_id)
    
    deleted_paths = []
    
    if os.path.exists(session_upload_path):
        shutil.rmtree(session_upload_path)
        deleted_paths.append(f"uploads/{session_id}")
    
    if os.path.exists(session_fragment_path):
        shutil.rmtree(session_fragment_path)
        deleted_paths.append(f"fragments/{session_id}")
        
    if not deleted_paths:
        return jsonify({"message": "No files found for this session to clean up."}), 404
        
    return jsonify({"message": "Session files cleaned up successfully", "deleted": deleted_paths}), 200


if __name__ == '__main__':
    # Nota: En un entorno de producción con Gunicorn y Nginx, esta sección no se usa.
    # Solo es para desarrollo local.
    app.run(debug=True, host='0.0.0.0', port=5000)