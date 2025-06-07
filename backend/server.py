import os
import time
import zipfile
import threading
from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    url_for,
    render_template,
)
from flask_cors import CORS
from moviepy.editor import VideoFileClip
from werkzeug.utils import secure_filename
import queue
import sys


# --- Inicialización de la Aplicación Flask ---
app = Flask(__name__)
CORS(app)

# AÑADE ESTAS LÍNEAS AQUÍ:
app.config['SERVER_NAME'] = 'global-ads-agency.onrender.com' # O la IP/dominio donde se ejecuta tu servidor (ej. 'yourdomain.com:5000')
app.config['PREFERRED_URL_SCHEME'] = 'https' # O 'https' si estás usando HTTPS
# FIN DE LAS LÍNEAS A AÑADIR
app.secret_key = 'eb243c1aa0cd5ca592ba26c466e1ee0c' # ¡ADVERTENCIA!: CAMBIA ESTO EN PRODUCCIÓN

# --- Configuraciones de Carpetas ---
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'fragments'
ALLOWED_EXTENSIONS = {'mp4', 'webm', 'ogg'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

os.makedirs(os.path.join(os.getcwd(), UPLOAD_FOLDER), exist_ok=True)
os.makedirs(os.path.join(os.getcwd(), OUTPUT_FOLDER), exist_ok=True)

# --- INICIO DE CÓDIGO DE LIMPIEZA AUTOMÁTICA ---
CLEANUP_INTERVAL_SECONDS = 3600  # 1 hora (3600 segundos) para producción
FILE_LIFETIME_SECONDS = 3600  # 1 hora para archivos
_cleanup_thread_started = False

def cleanup_files():
    with app.app_context():
        now = time.time()
        full_upload_path = os.path.join(os.getcwd(), app.config['UPLOAD_FOLDER'])
        full_output_path = os.path.join(os.getcwd(), app.config['OUTPUT_FOLDER'])

        for folder_path in [full_upload_path, full_output_path]:
            if os.path.exists(folder_path):
                for filename in os.listdir(folder_path):
                    filepath = os.path.join(folder_path, filename)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > FILE_LIFETIME_SECONDS:
                            try:
                                os.remove(filepath)
                                print(f"Limpieza: Eliminado archivo antiguo de {os.path.basename(folder_path)}: {filename}")
                            except Exception as e:
                                print(f"Limpieza: Error al eliminar {filename} de {os.path.basename(folder_path)}: {e}")
        
        zip_path = os.path.join(os.getcwd(), 'fragmentos.zip')
        if os.path.exists(zip_path) and os.path.isfile(zip_path):
            file_age = now - os.path.getmtime(zip_path)
            if file_age > FILE_LIFETIME_SECONDS:
                try:
                    os.remove(zip_path)
                    print(f"Limpieza: Eliminado archivo ZIP antiguo: {zip_path}")
                except Exception as e:
                    print(f"Limpieza: Error al eliminar {zip_path}: {e}")

    threading.Timer(CLEANUP_INTERVAL_SECONDS, cleanup_files).start()

def start_cleanup_thread():
    global _cleanup_thread_started
    if not _cleanup_thread_started and (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
        print("Iniciando el servicio de limpieza automática de archivos...")
        threading.Timer(CLEANUP_INTERVAL_SECONDS, cleanup_files).start()
        _cleanup_thread_started = True

start_cleanup_thread()
# --- FIN DE CÓDIGO DE LIMPIEZA AUTOMÁTICA ---

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def split_video(video_path, segment_duration, queue_ref):
    fragment_filenames = []
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration
        
        num_segments = int(duration / segment_duration)
        if duration % segment_duration != 0:
            num_segments += 1

        queue_ref.put(f"message: Duración total del video: {duration:.2f} segundos. Se crearán aproximadamente {num_segments} fragmentos.")

        start_time = 0
        fragment_index = 1

        while start_time < duration:
            end_time = min(start_time + segment_duration, duration)
            
            fragment_filename = f"fragment_{fragment_index}.mp4"
            fragment_path = os.path.join(app.config['OUTPUT_FOLDER'], fragment_filename)
            
            subclip = clip.subclip(start_time, end_time)

            print(f"Moviepy - Building video {fragment_path} (segment {fragment_index}/{num_segments}).")
            queue_ref.put(f"message: Procesando fragmento {fragment_index} de {num_segments}...")
            
            subclip.write_videofile(
                fragment_path,
                codec="libx264",
                audio_codec="aac",
                logger='bar',
            )
            
            fragment_filenames.append(fragment_filename)
            start_time += segment_duration
            fragment_index += 1

            overall_percentage = (min(start_time, duration) / duration) * 100
            queue_ref.put(f"overall_progress: {overall_percentage:.2f}")
            print(f"Moviepy - Finished fragment {fragment_index-1} of {num_segments}.")
            
        clip.close()
        queue_ref.put("message: Todos los fragmentos creados.")
        # No envíes "overall_progress: 100.00" aquí directamente, lo haremos en generate()
        # para asegurarnos de que la lista de fragmentos se pueda generar DESPUÉS de que se hayan creado todos.

    except Exception as e:
        queue_ref.put(f"error: Error al procesar el video: {str(e)}")
        print(f"Error al procesar el video: {e}")
        fragment_filenames = []
    finally:
        # Asegúrate de que el archivo original se elimine DESPUÉS de que el progreso del 100% se haya enviado
        # y los fragmentos se hayan listado. Esto se maneja mejor en el hilo principal de generate().
        pass # Eliminamos la eliminación aquí para manejarla en generate() después de enviar la info al cliente
    
    return fragment_filenames # Retornamos la lista de fragmentos

# --- Ruta para servir el Frontend (la página principal) ---
@app.route('/')
def index():
    return render_template('index.html')

# --- Rutas de la API de tu Aplicación ---

@app.route('/api/split_video', methods=['POST'])
def upload_and_split():
    if 'video' not in request.files:
        return jsonify({'error': 'No se proporcionó ningún archivo de video'}), 400
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó ningún archivo'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Formato de archivo no permitido'}), 400

    filename = secure_filename(file.filename)
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(video_path)

    try:
        segment_duration = int(request.form.get('segment_duration', 3))
    except (ValueError, TypeError):
        # Eliminar el archivo subido si la duración es inválida
        if os.path.exists(video_path):
            os.remove(video_path)
        return jsonify({"error": "Duración del segmento inválida."}), 400

    # Cola para enviar mensajes de progreso al cliente (para SSE)
    progress_queue = queue.Queue()
    # Una cola para que el hilo de procesamiento pueda enviar la lista final de fragmentos
    # ya que no podemos retornar directamente un valor de un hilo
    final_fragments_queue = queue.Queue() 

    # Iniciar el procesamiento en un hilo separado
    # Pasamos la segunda cola también
    threading.Thread(
        target=lambda: final_fragments_queue.put(split_video(video_path, segment_duration, progress_queue))
    ).start()

    # Esto establece la conexión Server-Sent Events (SSE)
    def generate():
        fragments_generated_successfully = False
        while True:
            try:
                # Intentar obtener un mensaje de progreso
                msg = progress_queue.get(timeout=0.1) # Timeout más corto para ser más reactivo

                if msg.startswith("error:"):
                    yield f"data: {msg}\n\n"
                    break # Salir del bucle si hay un error fatal
                else:
                    yield f"data: {msg}\n\n"
                    # Si el mensaje indica que todo el procesamiento en moviepy está listo
                    # (el 100% lo enviaremos al final después de obtener los enlaces)
                    if msg.startswith("message: Todos los fragmentos creados."):
                        fragments_generated_successfully = True

            except queue.Empty:
                # Si la cola de progreso está vacía, y el procesamiento terminó,
                # intentar obtener el resultado final del hilo de procesamiento.
                if fragments_generated_successfully:
                    try:
                        # Obtener la lista final de fragmentos del otro hilo
                        fragment_filenames = final_fragments_queue.get(timeout=5) # Esperar un poco por la lista final
                        
                        with app.app_context(): # IMPORTE: Envolver url_for en el contexto de la aplicación
                            if fragment_filenames:
                                fragment_urls = [
                                    url_for('download_fragment', filename=fname, _external=True)
                                    for fname in fragment_filenames
                                ]
                                # Envía el 100% y luego los fragmentos
                                yield f"data: overall_progress: 100.00\n\n" # Aseguramos el 100% final
                                yield f"data: fragments:{jsonify(fragment_urls).get_data(as_text=True)}\n\n"
                            else:
                                yield f"data: error: Error al dividir el video o no se generaron fragmentos.\n\n"
                        break # Terminar la conexión SSE después de enviar los fragmentos
                    except queue.Empty:
                        # Si no hay resultados finales aún, seguir esperando o romper si se agota el tiempo
                        print("Advertencia: Procesamiento terminado, pero no se recibieron los fragmentos finales a tiempo.")
                        yield f"data: error: No se pudieron obtener los fragmentos finales.\n\n"
                        break
                    except Exception as e:
                        print(f"Error al generar URLs de fragmentos: {e}")
                        yield f"data: error: Error interno al finalizar: {str(e)}\n\n"
                        break
                pass # Continuar esperando mensajes de progreso

            except Exception as e:
                print(f"Error en el stream de progreso (general): {e}")
                yield f"data: error: Error en el stream de progreso: {str(e)}\n\n"
                break
        
        # Eliminar el archivo original después de que toda la comunicación con el cliente haya terminado
        time.sleep(1) # Pequeña pausa para asegurar que el último mensaje de SSE se procese
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
                print(f"Archivo original '{os.path.basename(video_path)}' eliminado después del procesamiento.")
            else:
                print(f"Archivo original '{os.path.basename(video_path)}' ya no existe al intentar eliminarlo.")
        except Exception as e:
            print(f"Error al eliminar el archivo original '{os.path.basename(video_path)}': {e}")


    return app.response_class(generate(), mimetype='text/event-stream')


@app.route('/download_fragment/<filename>')
def download_fragment(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

@app.route('/api/download_all', methods=['POST'])
def download_all_fragments():
    data = request.get_json()
    if not data or 'filenames' not in data or not isinstance(data['filenames'], list):
        return jsonify({'error': 'Lista de nombres de archivo no válida'}), 400

    filenames = data['filenames']
    zip_path = 'fragmentos.zip'
    zipf = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
    for filename in filenames:
        filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        if os.path.exists(filepath):
            zipf.write(filepath, filename)
    zipf.close()

    return send_from_directory(os.getcwd(), 'fragmentos.zip', as_attachment=True)

# --- Manejo de Errores ---
@app.errorhandler(404)
def page_not_found(e):
    return jsonify(error="Ruta no encontrada", message="La URL solicitada no existe."), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error="Error interno del servidor", message="Algo salió mal en el servidor."), 500

# --- Ejecución del Servidor (Solo para desarrollo local) ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)