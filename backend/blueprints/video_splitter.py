import os
import time
import zipfile
import threading
import queue
import sys 
import json 

from flask import Blueprint, request, jsonify, Response, send_from_directory, url_for, current_app, Flask # Importa Flask explícitamente
from moviepy.editor import VideoFileClip
from werkzeug.utils import secure_filename

# --- Define un Blueprint para el módulo de división de video ---
splitter_bp = Blueprint('splitter', __name__)

# --- Función auxiliar para verificar extensiones de archivo permitidas ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

# --- Lógica principal de división de video (ejecutada en un hilo separado) ---
def split_video_worker(video_path, segment_duration, progress_queue, final_fragments_queue, output_folder):
    fragment_filenames = []
    clip = None
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration
        
        num_segments = int(duration / segment_duration)
        if duration % segment_duration != 0:
            num_segments += 1

        progress_queue.put(f"message: Duración total del video: {duration:.2f} segundos. Se crearán aproximadamente {num_segments} fragmentos.")

        start_time = 0
        fragment_index = 1

        while start_time < duration:
            end_time = min(start_time + segment_duration, duration)
            
            fragment_filename = f"parte_{fragment_index}.mp4" 
            fragment_path = os.path.join(output_folder, fragment_filename) 
            
            subclip = clip.subclip(start_time, end_time)

            print(f"Moviepy - Construyendo video {fragment_path} (segmento {fragment_index}/{num_segments}).")
            progress_queue.put(f"message: Procesando fragmento {fragment_index} de {num_segments}...")
            
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
            progress_queue.put(f"overall_progress: {overall_percentage:.2f}")
            print(f"Moviepy - Fragmento {fragment_index-1} de {num_segments} terminado.")
            
        clip.close()
        progress_queue.put("message: Todos los fragmentos creados.")

    except Exception as e:
        progress_queue.put(f"error: Error al procesar el video: {str(e)}")
        print(f"Error al procesar el video: {e}")
        fragment_filenames = [] 
    finally:
        if clip:
            clip.close()
        final_fragments_queue.put(fragment_filenames) 

# --- Rutas API para el Módulo de División de Video ---

@splitter_bp.route('/api/split_video', methods=['POST'])
def upload_and_split():
    if 'video' not in request.files:
        return jsonify({'error': 'No se proporcionó ningún archivo de video'}), 400
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó ningún archivo'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Formato de archivo no permitido'}), 400

    filename = secure_filename(file.filename)
    video_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(video_path)

    try:
        segment_duration = int(request.form.get('segment_duration', 3))
    except (ValueError, TypeError):
        if os.path.exists(video_path):
            os.remove(video_path)
        return jsonify({"error": "Duración del segmento inválida."}), 400

    progress_queue = queue.Queue()
    final_fragments_queue = queue.Queue() 

    # *** Importante: Capturar la instancia de la aplicación actual ***
    # Esto es más seguro para usar test_request_context() en el generador.
    app_instance = current_app._get_current_object()

    threading.Thread(
        target=split_video_worker,
        args=(video_path, segment_duration, progress_queue, final_fragments_queue, app_instance.config['OUTPUT_FOLDER'])
    ).start()

    # Función generadora para Server-Sent Events (SSE)
    # *** Ahora acepta la instancia de la aplicación como argumento ***
    def generate(app_instance_for_context):
        fragments_generated_successfully = False
        while True:
            try:
                msg = progress_queue.get(timeout=0.1) 

                if msg.startswith("error:"):
                    yield f"data: {msg}\n\n"
                    break 
                else:
                    yield f"data: {msg}\n\n"
                    if msg.startswith("message: Todos los fragmentos creados."):
                        fragments_generated_successfully = True

            except queue.Empty:
                if fragments_generated_successfully:
                    try:
                        fragment_filenames = final_fragments_queue.get(timeout=5) 
                        
                        # *** Usamos app_instance_for_context para crear el contexto ***
                        with app_instance_for_context.app_context(): 
                            with app_instance_for_context.test_request_context(): 
                                if fragment_filenames:
                                    fragment_urls_with_names = []
                                    for fname in fragment_filenames:
                                        download_url = url_for('splitter.download_fragment', filename=fname, _external=True)
                                        preview_url = url_for('static', filename=f"fragments/{fname}") 
                                        fragment_urls_with_names.append({"download_url": download_url, "preview_url": preview_url, "filename": fname})
                                    
                                    yield f"data: overall_progress: 100.00\n\n" 
                                    # Mantenemos json.dumps() aquí, ya que url_for ya tiene su contexto.
                                    yield f"data: fragments:{json.dumps(fragment_urls_with_names)}\n\n"
                                else:
                                    yield f"data: error: Error al dividir el video o no se generaron fragmentos.\n\n"
                        break 
                    except queue.Empty:
                        print("Advertencia: Procesamiento terminado, pero no se recibieron los fragmentos finales a tiempo.")
                        yield f"data: error: No se pudieron obtener los fragmentos finales.\n\n"
                        break
                    except Exception as e:
                        print(f"Error al generar URLs de fragmentos: {e}")
                        yield f"data: error: Error interno al finalizar: {str(e)}\n\n"
                        break
                pass 

            except Exception as e:
                print(f"Error en el stream de progreso (general): {e}")
                yield f"data: error: Error en el stream de progreso: {str(e)}\n\n"
                break
        
        time.sleep(1) 
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
                print(f"Archivo original '{os.path.basename(video_path)}' eliminado después del procesamiento.")
            else:
                print(f"Archivo original '{os.path.basename(video_path)}' ya no existe al intentar eliminarlo.")
        except Exception as e:
            print(f"Error al eliminar el archivo original '{os.path.basename(video_path)}': {e}")


    # *** Se pasa la instancia de la aplicación a la función generadora ***
    return current_app.response_class(generate(app_instance), mimetype='text/event-stream')


@splitter_bp.route('/download_fragment/<filename>')
def download_fragment(filename):
    return send_from_directory(current_app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

@splitter_bp.route('/api/download_all', methods=['POST'])
def download_all_fragments():
    data = request.get_json()
    if not data or 'filenames' not in data or not isinstance(data['filenames'], list):
        return jsonify({'error': 'Lista de nombres de archivo no válida'}), 400

    filenames = data['filenames']
    zip_path = 'fragmentos.zip'
    zipf = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
    for filename in filenames:
        filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
        if os.path.exists(filepath):
            zipf.write(filepath, filename)
    zipf.close()

    return send_from_directory(os.getcwd(), 'fragmentos.zip', as_attachment=True)