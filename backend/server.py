import os
import time
import shutil
import glob
from flask import Flask, request, jsonify, send_from_directory, redirect, url_for, render_template
from moviepy.editor import VideoFileClip, CompositeVideoClip, concatenate_videoclips
from werkzeug.utils import secure_filename
import threading
import queue # Importamos la librería queue para la comunicación entre hilos
import sys # Para manejar la salida estándar

app = Flask(__name__, static_folder='../static', template_folder='../templates')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['FRAGMENT_FOLDER'] = 'fragments'
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 500  # 500 MB limit

# Crear las carpetas si no existen
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['FRAGMENT_FOLDER'], exist_ok=True)

# Cola para enviar mensajes de progreso al cliente (para SSE)
progress_queue = queue.Queue()

# --- Funciones de limpieza automática ---
def clean_old_files():
    print("Iniciando el servicio de limpieza automática de archivos...")
    while True:
        now = time.time()
        # Limpiar la carpeta de uploads
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > 3600: # Archivos de más de 1 hora
                try:
                    os.remove(filepath)
                    print(f"Archivo antiguo eliminado: {filepath}")
                except Exception as e:
                    print(f"Error al eliminar archivo antiguo {filepath}: {e}")

        # Limpiar la carpeta de fragments
        for filename in os.listdir(app.config['FRAGMENT_FOLDER']):
            filepath = os.path.join(app.config['FRAGMENT_FOLDER'], filename)
            if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > 3600: # Archivos de más de 1 hora
                try:
                    os.remove(filepath)
                    print(f"Fragmento antiguo eliminado: {filepath}")
                except Exception as e:
                    print(f"Error al eliminar fragmento antiguo {filepath}: {e}")

        time.sleep(3600) # Esperar 1 hora antes de la próxima limpieza

# Iniciar el hilo de limpieza
cleaner_thread = threading.Thread(target=clean_old_files, daemon=True)
cleaner_thread.start()

# --- Función de procesamiento de video ---
def split_video(video_path, segment_duration, queue_ref): # Añadimos queue_ref
    fragment_filenames = []
    try:
        clip = VideoFileClip(video_path)
        total_duration = clip.duration
        num_segments = int(total_duration / segment_duration)

        queue_ref.put(f"message: Duración total del video: {total_duration:.2f} segundos. Se crearán {num_segments} fragmentos.")

        for i in range(num_segments):
            start_time = i * segment_duration
            end_time = (i + 1) * segment_duration
            
            # Asegurarse de no exceder la duración total del video para el último segmento
            if end_time > total_duration:
                end_time = total_duration

            fragment_path = os.path.join(app.config['FRAGMENT_FOLDER'], f"fragment_{i+1}_{segment_duration}.mp4")
            fragment_filenames.append(os.path.basename(fragment_path))

            subclip = clip.subclip(start_time, end_time)
            
            # === AQUI ESTÁ EL CAMBIO CLAVE PARA EL PROGRESO ===
            def progress_callback(t):
                percentage = (t / (end_time - start_time)) * 100
                queue_ref.put(f"progress: {percentage:.2f}") # Enviar el progreso del fragmento actual
                sys.stdout.flush() # Asegurar que la salida se envíe inmediatamente

            print(f"Moviepy - Building video {fragment_path} (segment {i+1}/{num_segments}).")
            queue_ref.put(f"message: Procesando fragmento {i+1} de {num_segments}...")
            
            subclip.write_videofile(
                fragment_path,
                codec="libx264",
                audio_codec="aac",
                fps=24, # Mantener un fps fijo para consistencia, o usar clip.fps
                temp_audiofile=f"{fragment_path}_temp_audio.mp3", # Agregamos un archivo temporal para el audio
                logger='bar', # Usa la barra de progreso de moviepy en la consola del servidor
                # progress_callback=progress_callback # Habilitar si quieres progreso por cada subclip
            )
            # Moviepy imprime su propia barra de progreso. Para enviar progreso general:
            overall_percentage = ((i + 1) / num_segments) * 100
            queue_ref.put(f"overall_progress: {overall_percentage:.2f}") # Enviar el progreso general
            print(f"Moviepy - Finished fragment {i+1} of {num_segments}.")


        clip.close() # Es importante cerrar el clip para liberar recursos
        print(f"Moviepy - All fragments built.")
        queue_ref.put("message: Todos los fragmentos creados. Generando enlace de descarga...")

    except Exception as e:
        queue_ref.put(f"error: Error al procesar el video: {str(e)}")
        print(f"Error al procesar el video: {e}")
        return []

    finally:
        # Intentar eliminar el archivo original después de procesar
        # Se añadió un pequeño retardo y un try-except por el error de Windows 32
        time.sleep(2) # Dar tiempo al sistema operativo para liberar el archivo
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
                print(f"Archivo original '{os.path.basename(video_path)}' eliminado.")
            else:
                print(f"Archivo original '{os.path.basename(video_path)}' ya no existe.")
        except Exception as e:
            print(f"Error al eliminar el archivo original '{os.path.basename(video_path)}': {e}")
    
    return fragment_filenames

# --- Rutas de Flask ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/split_video', methods=['POST'])
def upload_and_split():
    if 'video' not in request.files:
        return jsonify({"error": "No se proporcionó ningún archivo de video."}), 400

    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo."}), 400

    if video_file:
        filename = secure_filename(video_file.filename)
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        video_file.save(video_path)

        try:
            segment_duration = float(request.form.get('segment_duration'))
        except (ValueError, TypeError):
            return jsonify({"error": "Duración del segmento inválida."}), 400

        # Vaciar la cola para un nuevo proceso
        while not progress_queue.empty():
            try:
                progress_queue.get_nowait()
            except queue.Empty:
                break
        
        # Iniciar el procesamiento en un hilo separado
        # La función de procesamiento ahora recibe la cola para enviar updates
        threading.Thread(target=lambda: split_video(video_path, segment_duration, progress_queue)).start()
        
        return jsonify({"message": "Procesamiento iniciado. Conéctate a /api/progress para actualizaciones."}), 202

@app.route('/api/progress')
def progress():
    def generate():
        while True:
            try:
                msg = progress_queue.get(timeout=1) # Esperar un mensaje hasta 1 segundo
                yield f"data: {msg}\n\n"
                if msg.startswith("overall_progress: 100.00") or msg.startswith("error:"):
                    break # Terminar la conexión cuando el proceso termine o haya un error
            except queue.Empty:
                # No hay mensajes, continuar esperando
                pass
            except Exception as e:
                print(f"Error en el stream de progreso: {e}")
                yield f"data: error: Error en el stream de progreso: {str(e)}\n\n"
                break
            
    return app.response_class(generate(), mimetype='text/event-stream')


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['FRAGMENT_FOLDER'], filename, as_attachment=True)

@app.route('/list_fragments/<segment_duration>')
def list_fragments(segment_duration):
    try:
        # Filtra solo los archivos que coincidan con el patrón de segment_duration
        # Asegúrate de que los archivos se nombren consistentemente como fragment_{i+1}_{segment_duration}.mp4
        pattern = f"fragment_*_{segment_duration}.mp4"
        fragment_files = [f for f in os.listdir(app.config['FRAGMENT_FOLDER']) if f.startswith(f"fragment_") and f.endswith(f"_{segment_duration}.mp4")]
        
        # Puedes querer ordenar los fragmentos numéricamente si es necesario
        fragment_files.sort(key=lambda x: int(x.split('_')[1]))

        if not fragment_files:
            return jsonify({"message": "No se encontraron fragmentos para esta duración.", "fragments": []}), 404
        
        # Generar URLs de descarga
        fragment_urls = [url_for('download_file', filename=f) for f in fragment_files]
        return jsonify({"fragments": fragment_urls})

    except Exception as e:
        print(f"Error al listar fragmentos: {e}")
        return jsonify({"error": f"Error al listar fragmentos: {e}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)