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
    after_this_request,
)
from flask_cors import CORS
from moviepy.editor import VideoFileClip
from werkzeug.utils import secure_filename
import queue
import shutil
import uuid # Para generar IDs de sesión únicos
import subprocess # Para depurar FFMPEG
from datetime import datetime, timedelta # Para manejo de tiempo

# --- Inicialización de la Aplicación Flask ---
app = Flask(__name__)
CORS(app)

# !ADVERTENCIA!: CAMBIA ESTO EN PRODUCCIÓN - ESTO ES UN EJEMPLO.
app.secret_key = 'tu_clave_secreta_aqui_CAMBIALA_EN_PRODUCCION' 

# >>>>> CONFIGURACIÓN CLAVE PARA GENERAR URLs <<<<<
# Configura el SERVER_NAME y PREFERRED_URL_SCHEME para que Flask pueda
# construir URLs correctamente fuera del contexto de una petición activa
# (especialmente útil para el streaming SSE y en producción detrás de un proxy).

# Si estás en DESARROLLO LOCAL (ejecutando en tu máquina):
app.config['SERVER_NAME'] = 'localhost:5000' # Asegúrate de que el puerto coincida con el que usas (ej. 5000)
app.config['PREFERRED_URL_SCHEME'] = 'http'

# Si estás en PRODUCCIÓN (ej. desplegado en Render, Heroku, etc.):
# Descomenta las siguientes líneas y reemplaza con tu dominio real.
# app.config['SERVER_NAME'] = 'tu-app-de-render.onrender.com' # Ejemplo: global-ads-agency.onrender.com
# app.config['PREFERRED_URL_SCHEME'] = 'https'
# >>>>> FIN CONFIGURACIÓN CLAVE <<<<<


# --- Configuraciones de Carpetas (Absolutas o Relativas al Directorio de la App) ---
# Usamos os.getcwd() para obtener el directorio de trabajo actual donde se ejecuta app.py
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
OUTPUT_FOLDER = os.path.join(os.getcwd(), 'fragments')
ZIP_FOLDER = os.getcwd() # Para los ZIPs temporales, pueden estar en el mismo nivel de la app

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['ZIP_FOLDER'] = ZIP_FOLDER

# Asegúrate de que las carpetas existan al iniciar la aplicación
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)


# --- Depuración de FFMPEG al inicio (mantener para ver la salida) ---
try:
    print(f"\n--- FFMPEG PATH Check ---")
    print(f"PYTHON'S OS.ENVIRON['PATH']: {os.environ.get('PATH')}") 
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, check=True)
    print("FFMPEG FOUND. Version info:")
    print(result.stdout)
    print(f"--- FFMPEG PATH Check END ---\n")
except FileNotFoundError:
    print("\n--- FFMPEG PATH Check ---")
    print("FFMPEG NOT FOUND in system PATH from Python.")
    print("Please ensure FFMPEG is installed and its bin directory is added to your system's PATH environmental variable.")
    print("You can download FFMPEG from: https://ffmpeg.org/download.html")
    print(f"--- FFMPEG PATH Check END ---\n")
except Exception as e:
    print(f"Error checking FFMPEG version: {e}")
# --- Fin Depuración FFMPEG ---


# --- Cola para la comunicación de progreso (SSE) ---
# Almacena colas de progreso por session_id para manejar múltiples usuarios concurrentes
progress_queues = {} 


# --- Funciones de Limpieza ---
def cleanup_session_folder(session_path):
    """Elimina una carpeta de sesión y su contenido."""
    if os.path.exists(session_path):
        try:
            shutil.rmtree(session_path)
            print(f"Carpeta de sesión eliminada: {session_path}")
        except OSError as e:
            print(f"Error al eliminar la carpeta de sesión {session_path}: {e}")

def cleanup_file(filepath):
    """Elimina un archivo específico."""
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            print(f"Archivo eliminado: {filepath}")
        except OSError as e:
            print(f"Error al eliminar archivo {filepath}: {e}")

def update_last_accessed(session_folder_path):
    """Actualiza la marca de tiempo de último acceso en la carpeta de sesión."""
    timestamp_file = os.path.join(session_folder_path, 'last_accessed.txt')
    try:
        with open(timestamp_file, 'w') as f:
            f.write(str(time.time()))
        # print(f"Marca de tiempo actualizada para: {session_folder_path}") # Descomentar para depuración
    except Exception as e:
        print(f"Error al actualizar la marca de tiempo en {session_folder_path}: {e}")


def deferred_cleanup_thread(original_filepath, delay_seconds=30):
    """
    Hilo para la limpieza diferida del archivo original subido.
    La limpieza de la carpeta de sesión se gestiona ahora por el hilo de respaldo
    basado en inactividad.
    """
    time.sleep(delay_seconds)
    cleanup_file(original_filepath)


# --- Hilo de limpieza de respaldo para fragments y uploads (AHORA CON INACTIVIDAD) ---
def background_cleanup_thread():
    """
    Hilo que periódicamente limpia directorios de fragments y uploads.
    Elimina carpetas de sesión si no han tenido actividad en un tiempo.
    """
    CLEANUP_INTERVAL_SECONDS = 60 # Frecuencia con la que el hilo revisa las carpetas (1 minuto)
    INACTIVITY_THRESHOLD_SECONDS = 60 # Tiempo de inactividad para eliminar la carpeta (1 minuto)

    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        print("Iniciando limpieza de respaldo de fragments y uploads (por inactividad)...")
        
        # Limpiar carpetas de fragments por sesión
        for session_dir_name in os.listdir(app.config['OUTPUT_FOLDER']):
            session_path = os.path.join(app.config['OUTPUT_FOLDER'], session_dir_name)
            
            if os.path.isdir(session_path):
                timestamp_file = os.path.join(session_path, 'last_accessed.txt')
                
                last_accessed_time = 0.0
                if os.path.exists(timestamp_file):
                    try:
                        with open(timestamp_file, 'r') as f:
                            last_accessed_time = float(f.read().strip())
                    except Exception as e:
                        print(f"Error al leer la marca de tiempo de {timestamp_file}: {e}. Se tratará como inactiva.")
                        last_accessed_time = 0.0 # Tratar como inactiva si hay error de lectura
                else:
                    # Si no hay archivo de marca de tiempo, usar la hora de creación/modificación de la carpeta
                    # Esto cubre el caso de carpetas antiguas o donde el archivo no se pudo crear.
                    try:
                        last_accessed_time = os.path.getmtime(session_path)
                    except OSError as e:
                        print(f"Error al obtener mtime de {session_path}: {e}. Se saltará esta carpeta.")
                        continue # Saltar esta carpeta si no se puede obtener la hora

                # Si la inactividad excede el umbral, eliminar la carpeta
                if (time.time() - last_accessed_time) > INACTIVITY_THRESHOLD_SECONDS:
                    cleanup_session_folder(session_path)

        # Limpiar archivos individuales en la carpeta de uploads (igual que antes, por antigüedad)
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(file_path):
                try:
                    # Si el archivo tiene más de 1 minuto (para pruebas) o 24 horas (producción), eliminarlo
                    # Mantendremos 1 minuto aquí si quieres probar la limpieza rápida
                    if (time.time() - os.path.getmtime(file_path)) > 60: # 60 segundos (1 minuto)
                        os.remove(file_path)
                        print(f"Archivo de carga antiguo eliminado: {file_path}")
                except OSError as e:
                    print(f"Error al eliminar archivo de carga {file_path}: {e}")

        print("Limpieza de respaldo finalizada.")


# Iniciar el hilo de limpieza de respaldo al inicio de la aplicación
cleanup_thread = threading.Thread(target=background_cleanup_thread)
cleanup_thread.daemon = True  # Permite que el hilo se cierre cuando la app principal lo haga
cleanup_thread.start()


# --- Rutas de la Aplicación ---
@app.route('/')
def index():
    """Renderiza la página principal de la aplicación."""
    return render_template('index.html')

@app.route('/api/split_video', methods=['POST'])
def upload_and_split():
    """
    Maneja la subida de videos, inicia el proceso de división y envía actualizaciones
    de progreso a través de Server-Sent Events (SSE).
    """
    if 'video' not in request.files:
        return jsonify({"error": "No se proporcionó ningún archivo de video"}), 400

    video_file = request.files['video']
    segment_duration_str = request.form.get('segment_duration', '60')

    try:
        segment_duration = int(segment_duration_str)
        if segment_duration <= 0:
            return jsonify({"error": "La duración del segmento debe ser un número positivo."}), 400
    except ValueError:
        return jsonify({"error": "La duración del segmento debe ser un número entero."}), 400

    if video_file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    filename = secure_filename(video_file.filename)
    original_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    video_file.save(original_filepath)

    session_id = str(uuid.uuid4())
    session_output_folder = os.path.join(app.config['OUTPUT_FOLDER'], session_id)
    os.makedirs(session_output_folder, exist_ok=True)
    
    # Inicializa la marca de tiempo de la sesión
    update_last_accessed(session_output_folder)

    progress_queue = queue.Queue()
    progress_queues[session_id] = progress_queue

    relative_fragment_paths = []

    # Iniciar el procesamiento del video en un hilo separado
    processing_thread = threading.Thread(
        target=process_video_in_background,
        args=(original_filepath, session_output_folder, segment_duration, progress_queue, relative_fragment_paths)
    )
    processing_thread.start()

    # Función generadora para Server-Sent Events (SSE)
    def generate():
        while True:
            try:
                # Intenta obtener un mensaje de la cola de progreso
                msg = progress_queue.get(timeout=5) # Timeout de 5 segundos
                
                # Cada vez que haya un mensaje de progreso (lo que indica actividad), actualiza la marca de tiempo
                # Esto es crucial para mantener la sesión "viva" durante el procesamiento.
                if session_id in progress_queues: # Asegurarse de que la cola exista antes de actualizar
                     update_last_accessed(session_output_folder)

                if msg.startswith("error:"):
                    yield f"data: {msg}\n\n"
                    # Si hay un error, el procesamiento falló, señalamos la limpieza del video original.
                    cleanup_thread_deferred = threading.Thread(
                        target=deferred_cleanup_thread, 
                        args=(original_filepath, 30) # Solo limpia el archivo original subido
                    )
                    cleanup_thread_deferred.daemon = True
                    cleanup_thread_deferred.start()
                    yield f"data: cleanup_signal:true\n\n" 
                    break 
                elif msg == "processing_complete": # Señal de que el procesamiento asíncrono ha terminado
                    with app.app_context(): 
                        if relative_fragment_paths:
                            fragment_urls = [
                                url_for('download_fragment', filename=rel_path, _external=False)
                                for rel_path in relative_fragment_paths
                            ]
                            yield f"data: overall_progress: 100.00\n\n"
                            yield f"data: fragments:{jsonify(fragment_urls).get_data(as_text=True)}\n\n"
                        else:
                            yield f"data: error: Error al dividir el video o no se generaron fragmentos.\n\n"
                    
                    # Una vez que los fragmentos y el progreso final se han enviado,
                    # iniciamos la limpieza diferida SOLO del archivo original.
                    cleanup_thread_deferred = threading.Thread(
                        target=deferred_cleanup_thread, 
                        args=(original_filepath, 30) # Solo limpia el archivo original subido
                    )
                    cleanup_thread_deferred.daemon = True
                    cleanup_thread_deferred.start()

                    yield f"data: cleanup_signal:true\n\n" # Envía una señal al frontend
                    break # Salir del bucle SSE
                else: # Es un mensaje de progreso o info
                    yield f"data: {msg}\n\n"

            except queue.Empty:
                # Si la cola está vacía por un tiempo, y el procesamiento aún no ha enviado "processing_complete",
                # el generador sigue esperando. No es un error. Aquí se podría añadir un ping/keepalive si se desea.
                # Asegurarse de que la marca de tiempo se actualice incluso en inactividad de la cola
                if session_id in progress_queues:
                    update_last_accessed(session_output_folder)

            except Exception as e:
                print(f"Error en el stream de progreso (general): {e}")
                yield f"data: error: Error en el stream de progreso: {str(e)}\n\n"
                # En caso de error inesperado en el generador, también señaliza y dispara la limpieza del original.
                cleanup_thread_deferred = threading.Thread(
                    target=deferred_cleanup_thread, 
                    args=(original_filepath, 30) # Solo limpia el archivo original subido
                )
                cleanup_thread_deferred.daemon = True
                cleanup_thread_deferred.start()
                yield f"data: cleanup_signal:true\n\n" 
                break
        
        # Libera la cola de progreso de esta sesión del diccionario global.
        if session_id in progress_queues:
            del progress_queues[session_id]

    # Retorna la respuesta de Server-Sent Events
    return app.response_class(generate(), mimetype='text/event-stream')


def process_video_in_background(original_filepath, session_output_folder, segment_duration, progress_queue, relative_fragment_paths):
    """
    Función que realiza el procesamiento de división de video en un hilo separado.
    Comunica el progreso y los errores a través de la cola.
    """
    try:
        # --- NUEVO ENFOQUE: Obtener duración y luego procesar fragmentos ---
        # Primero, obtener la duración del video sin mantener el clip abierto
        total_duration = 0
        try:
            temp_clip_for_duration = VideoFileClip(original_filepath)
            total_duration = temp_clip_for_duration.duration
            temp_clip_for_duration.close() # ¡CERRAR INMEDIATAMENTE!
            del temp_clip_for_duration # Liberar referencia
        except Exception as e:
            error_message = f"Error al obtener la duración del video: {str(e)}. Verifique el archivo de video."
            progress_queue.put(f"error: {error_message}")
            print(f"Error al obtener duración del video: {error_message}")
            progress_queue.put("processing_complete")
            return

        num_segments = int(total_duration // segment_duration)
        if total_duration % segment_duration > 0:
            num_segments += 1

        progress_queue.put(f"message: Duración total: {total_duration:.2f} segundos. Segmentos esperados: {num_segments}")

        # Itera para crear cada segmento
        for i in range(num_segments):
            start_time = i * segment_duration
            end_time = min((i + 1) * segment_duration, total_duration)
            
            fragment_filename = f"fragment_{i+1}.mp4"
            output_filepath = os.path.join(session_output_folder, fragment_filename)

            progress_queue.put(f"message: Procesando segmento {i+1}/{num_segments} ({start_time:.2f}-{end_time:.2f}s)")
            
            current_subclip_main_clip = None # Inicializar para el finally interno de cada fragmento
            try:
                # >>> CAMBIO CLAVE: Re-abre el archivo original para CADA fragmento <<<
                current_subclip_main_clip = VideoFileClip(original_filepath)
                
                subclip = current_subclip_main_clip.subclip(start_time, end_time)
                
                # Se utiliza fps del clip recién abierto, aunque para subclip no siempre es crítico
                # si el video original tiene una tasa de frames constante.
                subclip.write_videofile(
                    output_filepath, 
                    codec="libx264", 
                    audio_codec="aac", 
                    fps=current_subclip_main_clip.fps, 
                    logger=None # Suprime la salida verbosa de moviepy
                )
                subclip.close() # Importante: cierra el subclip generado
            except Exception as e:
                # Si falla la creación de un fragmento, notifica el error y detiene el proceso.
                error_msg = f"Error al crear fragmento {i+1}: {str(e)}. Asegúrate de que FFMPEG está instalado y en tu PATH."
                progress_queue.put(f"error: {error_msg}")
                print(f"Error en moviepy al crear fragmento {i+1}: {e}")
                # En caso de error, el `finally` interno se encargará de cerrar `current_subclip_main_clip`
                progress_queue.put("processing_complete") # Señalizar completado (con error)
                return # Detener el procesamiento en caso de error grave
            finally:
                # Asegura que el clip principal reabierto para ESTE fragmento se cierre
                if current_subclip_main_clip:
                    current_subclip_main_clip.close()
                    # print(f"Clip principal cerrado para fragmento {i+1}") # Descomentar para depuración

            relative_path_for_url = os.path.join(os.path.basename(session_output_folder), fragment_filename)
            relative_fragment_paths.append(relative_path_for_url)
            
            percentage = ((i + 1) / num_segments) * 100
            progress_queue.put(f"overall_progress: {percentage:.2f}")

        # Señal de que el procesamiento asíncrono ha completado exitosamente.
        progress_queue.put("processing_complete") 
        progress_queue.put(f"message: Todos los fragmentos creados.") # Mensaje final informativo
        progress_queue.put(f"overall_progress: 100.00") # Asegurar 100% final

    except Exception as e:
        # Captura errores generales durante el procesamiento del video (ej. archivo corrupto, MoviePy no puede cargar)
        error_message = f"Error interno durante el procesamiento del video: {str(e)}. Verifique el archivo o la instalación de MoviePy/FFMPEG."
        print(f"Error en process_video_in_background: {error_message}")
        progress_queue.put(f"error: {error_message}")
        # En caso de error general, también enviamos la señal de completado (aunque sea por error)
        progress_queue.put("processing_complete") 
    finally:
        # Este finally global ya no necesita cerrar 'clip' porque se maneja por fragmento.
        # Se mantiene por si se añaden otros recursos globales en el futuro.
        pass


@app.route('/fragments/<path:filename>')
def download_fragment(filename):
    """
    Permite la descarga de un fragmento de video individual.
    La 'filename' incluye el ID de sesión y el nombre del archivo (ej. 'session_id/fragment_1.mp4').
    """
    first_slash_index = filename.find(os.sep)
    if first_slash_index == -1:
        # Esto maneja el caso de rutas con barras invertidas de Windows en el log.
        # Flask en Windows puede enviar rutas con '\' en lugar de '/'.
        # Es mejor normalizar a '/' para la lógica.
        filename = filename.replace('\\', '/')
        first_slash_index = filename.find('/')
        if first_slash_index == -1:
            return jsonify({"error": "Ruta de fragmento inválida"}), 400

    session_folder_name = filename[:first_slash_index]
    file_relative_path_in_session = filename[first_slash_index + 1:]

    full_session_path = os.path.join(app.config['OUTPUT_FOLDER'], session_folder_name)
    
    # --- Actualiza la marca de tiempo de la sesión al acceder a un fragmento ---
    update_last_accessed(full_session_path)

    try:
        return send_from_directory(full_session_path, file_relative_path_in_session, as_attachment=False)
    except Exception as e:
        print(f"Error al servir fragmento {filename}: {e}")
        return jsonify({"error": "Fragmento no encontrado o error al servir."}), 404


@app.route('/api/download_all', methods=['POST'])
def download_all_fragments():
    """
    Genera un archivo ZIP con todos los fragmentos de una sesión y lo envía para descarga.
    Elimina el ZIP temporal después de la descarga.
    """
    data = request.get_json()
    filenames_with_session_id = data.get('filenames', []) 

    if not filenames_with_session_id:
        return jsonify({"error": "No se proporcionaron nombres de archivo para descargar"}), 400

    if not filenames_with_session_id[0]:
        return jsonify({"error": "Nombre de archivo vacío"}), 400

    # Normalizar la primera ruta para extraer el session_id, en caso de rutas con '\'
    first_filename_normalized = filenames_with_session_id[0].replace('\\', '/')
    first_slash_index = first_filename_normalized.find('/')
    if first_slash_index == -1:
        return jsonify({"error": "Formato de nombre de archivo inválido para ZIP (falta session_id)"}), 400
    
    session_id = first_filename_normalized[:first_slash_index]
    session_output_folder = os.path.join(app.config['OUTPUT_FOLDER'], session_id)

    if not os.path.exists(session_output_folder):
        return jsonify({"error": "Carpeta de sesión no encontrada"}), 404

    zip_filename = f"fragmentos_{session_id}.zip"
    zip_filepath = os.path.join(app.config['ZIP_FOLDER'], zip_filename)

    # --- Actualiza la marca de tiempo de la sesión al generar el ZIP ---
    update_last_accessed(session_output_folder)

    try:
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for rel_path_with_session_id in filenames_with_session_id:
                # Normalizar la ruta del archivo a añadir al ZIP
                rel_path_normalized = rel_path_with_session_id.replace('\\', '/')
                file_name_only = rel_path_normalized[rel_path_normalized.find('/') + 1:]
                file_to_zip_path = os.path.join(session_output_folder, file_name_only)
                
                if os.path.exists(file_to_zip_path) and os.path.isfile(file_to_zip_path):
                    zipf.write(file_to_zip_path, arcname=file_name_only)
                else:
                    print(f"Advertencia: Archivo no encontrado para ZIP: {file_to_zip_path}")

        @after_this_request
        def remove_zip_file(response):
            if os.path.exists(zip_filepath):
                try:
                    os.remove(zip_filepath)
                    print(f"Archivo ZIP temporal eliminado: {zip_filepath}")
                except OSError as e:
                    print(f"Error al eliminar el archivo ZIP {zip_filepath}: {e}")
            return response

        return send_from_directory(app.config['ZIP_FOLDER'], zip_filename, as_attachment=True)

    except Exception as e:
        print(f"Error al crear o servir el archivo ZIP: {e}")
        return jsonify({"error": f"Error al crear o descargar el archivo ZIP: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, threaded=True)