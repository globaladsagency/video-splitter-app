import os
import time
import zipfile
import threading
from flask import Flask, request, jsonify, send_from_directory, redirect, url_for, render_template # Eliminado flash, session, UserMixin etc. si no se usan
from flask_cors import CORS
from moviepy.editor import VideoFileClip
from werkzeug.utils import secure_filename

# --- Inicialización de la Aplicación Flask ---
# Flask buscará 'templates' y 'static' en la misma carpeta que este server.py por defecto.
# Ya que server.py está en 'backend/', estas carpetas deben estar dentro de 'backend/'.
app = Flask(__name__)
CORS(app) # Habilitar CORS para permitir solicitudes desde el frontend

# Configuración de la clave secreta para la sesión de Flask.
# Aunque tu aplicación actual no usa sesiones de Flask, es buena práctica tenerla.
# ¡ADVERTENCIA!: En un entorno de producción REAL, NUNCA debes hardcodear
# la clave secreta directamente en el código como aquí.
# Siempre cárgala desde una variable de entorno para mayor seguridad.
app.secret_key = 'Ryuk1998*' # CAMBIA ESTO

# --- Configuraciones de Carpetas ---
# Estas rutas son relativas al directorio donde se ejecuta server.py,
# que con el `--chdir backend` de Gunicorn, será la carpeta 'backend'.
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'fragments'
ALLOWED_EXTENSIONS = {'mp4', 'webm', 'ogg'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

# Asegurarse de que las carpetas existan al iniciar la aplicación.
# Se crearán dentro de 'backend/' gracias al --chdir de Gunicorn.
os.makedirs(os.path.join(os.getcwd(), UPLOAD_FOLDER), exist_ok=True)
os.makedirs(os.path.join(os.getcwd(), OUTPUT_FOLDER), exist_ok=True)

# --- INICIO DE CÓDIGO DE LIMPIEZA AUTOMÁTICA ---
CLEANUP_INTERVAL_SECONDS = 3600  # 1 hora (3600 segundos) para producción
FILE_LIFETIME_SECONDS = 3600     # 1 hora para archivos
_cleanup_thread_started = False 

def cleanup_files():
    with app.app_context(): # Asegura que la función se ejecute dentro del contexto de la app Flask
        now = time.time()
        
        # Rutas completas a las carpetas (relativas al directorio de trabajo actual)
        full_upload_path = os.path.join(os.getcwd(), app.config['UPLOAD_FOLDER'])
        full_output_path = os.path.join(os.getcwd(), app.config['OUTPUT_FOLDER'])

        for folder_path in [full_upload_path, full_output_path]:
            if os.path.exists(folder_path):
                for filename in os.listdir(folder_path):
                    filepath = os.path.join(folder_path, filename)
                    if os.path.isfile(filepath): # Solo si es un archivo (no subcarpetas)
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > FILE_LIFETIME_SECONDS:
                            try:
                                os.remove(filepath)
                                print(f"Limpieza: Eliminado archivo antiguo de {os.path.basename(folder_path)}: {filename}")
                            except Exception as e:
                                print(f"Limpieza: Error al eliminar {filename} de {os.path.basename(folder_path)}: {e}")
                            
        # Limpiar el archivo ZIP principal (fragmentos.zip)
        # Este archivo se crea en el directorio de trabajo actual (backend/)
        zip_path = os.path.join(os.getcwd(), 'fragmentos.zip')
        if os.path.exists(zip_path) and os.path.isfile(zip_path):
            file_age = now - os.path.getmtime(zip_path)
            if file_age > FILE_LIFETIME_SECONDS:
                try:
                    os.remove(zip_path)
                    print(f"Limpieza: Eliminado archivo ZIP antiguo: {zip_path}")
                except Exception as e:
                    print(f"Limpieza: Error al eliminar {zip_path}: {e}")

    # Programa la próxima ejecución de la limpieza
    threading.Timer(CLEANUP_INTERVAL_SECONDS, cleanup_files).start()

def start_cleanup_thread():
    global _cleanup_thread_started
    # Solo inicia el hilo si no se ha iniciado ya y si no es el proceso de recarga de Flask en modo debug
    if not _cleanup_thread_started and (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
        print("Iniciando el servicio de limpieza automática de archivos...")
        # Llama a cleanup_files() por primera vez para iniciar el ciclo
        threading.Timer(CLEANUP_INTERVAL_SECONDS, cleanup_files).start()
        _cleanup_thread_started = True

# Inicia el hilo de limpieza cuando la aplicación se carga en Gunicorn (producción)
# o en el proceso principal de Werkzeug (desarrollo con debug=True)
start_cleanup_thread()
# --- FIN DE CÓDIGO DE LIMPIEZA AUTOMÁTICA ---


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def split_video(video_path, segment_duration):
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration
        fragment_filenames = []
        start_time = 0
        fragment_index = 1

        while start_time < duration:
            end_time = min(start_time + segment_duration, duration)
            subclip = clip.subclip(start_time, end_time)
            fragment_filename = f"fragment_{fragment_index}.mp4"
            fragment_path = os.path.join(app.config['OUTPUT_FOLDER'], fragment_filename)
            subclip.write_videofile(fragment_path, codec="libx264", audio_codec="aac")
            fragment_filenames.append(fragment_filename)
            start_time += segment_duration
            fragment_index += 1

        clip.close()
        return fragment_filenames
    except Exception as e:
        print(f"Error al procesar el video: {e}")
        return None

# --- Ruta para servir el Frontend (la página principal) ---
@app.route('/')
def index():
    """
    Sirve el archivo HTML principal de tu aplicación frontend.
    Flask lo buscará en la carpeta 'templates' por defecto.
    """
    return render_template('index.html')

# --- Rutas de la API de tu Aplicación ---

@app.route('/api/split_video', methods=['POST'])
def upload_and_split():
    if 'video' not in request.files:
        return jsonify({'error': 'No se proporcionó ningún archivo de video'}), 400
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó ningún archivo'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(video_path)
        segment_duration = int(request.form.get('segment_duration', 3))

        fragment_filenames = split_video(video_path, segment_duration)

        time.sleep(2) # Pequeña pausa para asegurar la escritura del archivo
        try:
            os.remove(video_path) 
            print(f"Video original '{filename}' eliminado después del procesamiento.")
        except Exception as e:
            print(f"Error al eliminar el archivo original '{filename}': {e}")
            pass

        if fragment_filenames:
            # ¡IMPORTANTE!: Usar url_for para generar URLs que funcionen en Render
            # _external=True generará la URL completa con el dominio del despliegue
            fragment_urls = [url_for('download_fragment', filename=fname, _external=True) for fname in fragment_filenames]
            return jsonify({'fragment_urls': fragment_urls, 'fragment_filenames': fragment_filenames}), 200
        else:
            return jsonify({'error': 'Error al dividir el video'}), 500
    else:
        return jsonify({'error': 'Formato de archivo no permitido'}), 400

@app.route('/download_fragment/<filename>')
def download_fragment(filename):
    # send_from_directory servirá el archivo desde la carpeta OUTPUT_FOLDER
    # que está dentro de backend/ (gracias a `--chdir backend` de Gunicorn)
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

@app.route('/api/download_all', methods=['POST'])
def download_all_fragments():
    data = request.get_json()
    if not data or 'filenames' not in data or not isinstance(data['filenames'], list):
        return jsonify({'error': 'Lista de nombres de archivo no válida'}), 400

    filenames = data['filenames']
    # El archivo ZIP se creará en el directorio de trabajo actual (backend/)
    zip_path = 'fragmentos.zip' 
    zipf = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
    for filename in filenames:
        filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        if os.path.exists(filepath):
            zipf.write(filepath, filename)
    zipf.close()

    # Sirve el archivo ZIP desde el directorio de trabajo actual (backend/)
    return send_from_directory(os.getcwd(), 'fragmentos.zip', as_attachment=True)

# --- Manejo de Errores (Opcional, pero recomendado para producción) ---
@app.errorhandler(404)
def page_not_found(e):
    return jsonify(error="Ruta no encontrada", message="La URL solicitada no existe."), 404

@app.errorhandler(500)
def internal_server_error(e):
    # En un entorno de producción, no exponer detalles internos del error directamente.
    return jsonify(error="Error interno del servidor", message="Algo salió mal en el servidor."), 500


# --- Ejecución del Servidor (Solo para desarrollo local) ---
# Este bloque de código solo se ejecuta cuando corres `python server.py`
# directamente en tu máquina local. Render (Gunicorn) se encarga de iniciar
# la aplicación por sí mismo, por lo que esta sección será ignorada en Render.
if __name__ == '__main__':
    # Flask tomará el puerto de la variable de entorno 'PORT' de Render,
    # o usará el puerto 5000 si se ejecuta localmente.
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)