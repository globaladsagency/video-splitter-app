// script.js
const API_BASE_URL = window.location.origin; // Esto detecta automáticamente "http://tu_ip" o "https://tu_dominio"

document.getElementById('uploadForm').addEventListener('submit', async function(event) {
    event.preventDefault(); // Evita el envío del formulario tradicional

    const videoInput = document.getElementById('videoFile');
    const chunkDurationInput = document.getElementById('chunkDuration');
    const statusDiv = document.getElementById('status');
    const resultsDiv = document.getElementById('results');
    const progressBar = document.getElementById('progressBar');

    statusDiv.textContent = '';
    resultsDiv.innerHTML = '';
    progressBar.style.width = '0%';
    progressBar.textContent = '0%';
    progressBar.style.backgroundColor = '#4CAF50'; // Reset color

    if (videoInput.files.length === 0) {
        statusDiv.textContent = 'Please select a video file.';
        return;
    }

    const formData = new FormData();
    formData.append('video', videoInput.files[0]);
    formData.append('chunkDuration', chunkDurationInput.value);

    try {
        statusDiv.textContent = 'Uploading video...';
        const response = await fetch(`${API_BASE_URL}/api/split_video`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        const taskId = data.task_id;
        const sessionId = data.session_id; // Recibimos el session_id desde el inicio
        statusDiv.textContent = `Video uploaded. Processing started (Task ID: ${taskId}). Polling status...`;
        
        // Iniciar el sondeo para el estado de la tarea
        pollTaskStatus(taskId, sessionId);

    } catch (error) {
        console.error('Error:', error);
        statusDiv.textContent = `Error: ${error.message}`;
        progressBar.style.width = '0%'; // Reset progress bar on error
        progressBar.textContent = '0%';
        progressBar.style.backgroundColor = '#f44336'; // Red for error
    }
});


function pollTaskStatus(taskId, sessionId) {
    const statusDiv = document.getElementById('status');
    const progressBar = document.getElementById('progressBar');
    let pollInterval;

    pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/api/task_status/${taskId}`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            statusDiv.textContent = `Status: ${data.status}`;
            
            if (data.progress) {
                progressBar.style.width = data.progress;
                progressBar.textContent = data.progress;
            }

            if (data.state === 'SUCCESS') {
                clearInterval(pollInterval);
                statusDiv.textContent = `Status: ${data.status}`;
                progressBar.style.width = '100%';
                progressBar.textContent = '100%';
                progressBar.style.backgroundColor = '#4CAF50'; // Green for success
                displayDownloadLinks(data.fragments, data.session_id); // Usamos data.session_id
                // Opcional: Limpiar archivos después de un tiempo o con un botón
                // setTimeout(() => cleanupSession(sessionId), 300000); // Limpiar después de 5 minutos
            } else if (data.state === 'FAILURE') {
                clearInterval(pollInterval);
                statusDiv.textContent = `Error: ${data.status}. Check server logs for details.`;
                progressBar.style.width = '0%'; // Reset progress bar on error
                progressBar.textContent = 'Error';
                progressBar.style.backgroundColor = '#f44336'; // Red for error
            }

        } catch (error) {
            clearInterval(pollInterval);
            console.error('Error polling status:', error);
            statusDiv.textContent = `Error polling status: ${error.message}`;
            progressBar.style.width = '0%'; // Reset progress bar on error
            progressBar.textContent = 'Error';
            progressBar.style.backgroundColor = '#f44336'; // Red for error
        }
    }, 2000); // Sondeo cada 2 segundos
}


function displayDownloadLinks(fragments, sessionId) {
    const resultsDiv = document.getElementById('results');
    resultsDiv.innerHTML = '<h3>Generated Fragments:</h3>';

    if (!fragments || fragments.length === 0) {
        resultsDiv.innerHTML += '<p>No fragments generated or found.</p>';
        return;
    }

    fragments.forEach(fragment => {
        const fragmentDiv = document.createElement('div');
        fragmentDiv.className = 'fragment-item';

        // 1. Elemento de video para previsualización
        const videoElement = document.createElement('video');
        videoElement.src = fragment.url; // Usa la URL proporcionada por el backend
        videoElement.controls = true; // Permite controles de reproducción
        videoElement.loop = true; // Bucle para previsualización
        videoElement.preload = 'metadata'; // Carga solo metadatos inicialmente
        videoElement.style.maxWidth = '100%';
        videoElement.style.height = 'auto';
        videoElement.style.display = 'block'; // Asegura que ocupe su propia línea
        videoElement.style.marginBottom = '10px';
        videoElement.title = `Preview of ${fragment.name}`;

        // Añadir manejo de error para el video
        videoElement.addEventListener('error', (e) => {
            console.error(`Error loading video ${fragment.url}:`, e);
            // Muestra un mensaje amigable o una imagen de placeholder
            const errorMsg = document.createElement('p');
            errorMsg.style.color = 'red';
            errorMsg.textContent = `Could not load preview for ${fragment.name}. It might require HTTPS or the file could not be found.`;
            fragmentDiv.insertBefore(errorMsg, videoElement.nextSibling);
            // Si el error es por HTTPS, el navegador lo mostrará en la consola.
            // Aquí podemos dar una pista al usuario.
            if (window.location.protocol === 'http:' && fragment.url.startsWith('http:')) {
                errorMsg.textContent += " (Consider loading your site over HTTPS for video playback)";
            }
        });


        // 2. Enlace de descarga
        const downloadLink = document.createElement('a');
        downloadLink.href = fragment.url; // Usa la URL proporcionada por el backend
        downloadLink.textContent = `Download ${fragment.name}`;
        downloadLink.download = fragment.name; // Sugiere el nombre del archivo al descargar
        downloadLink.className = 'download-link';


        fragmentDiv.appendChild(videoElement);
        fragmentDiv.appendChild(downloadLink);
        resultsDiv.appendChild(fragmentDiv);
    });

    // Añadir botón de limpieza
    const cleanupBtn = document.createElement('button');
    cleanupBtn.textContent = 'Clean Up Session Files';
    cleanupBtn.className = 'cleanup-btn';
    cleanupBtn.onclick = () => cleanupSession(sessionId);
    resultsDiv.appendChild(cleanupBtn);
}


async function cleanupSession(sessionId) {
    const statusDiv = document.getElementById('status');
    try {
        statusDiv.textContent = 'Cleaning up session files...';
        const response = await fetch(`${API_BASE_URL}/api/cleanup/${sessionId}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        statusDiv.textContent = data.message;
        document.getElementById('results').innerHTML = ''; // Limpiar resultados
    } catch (error) {
        console.error('Error cleaning up:', error);
        statusDiv.textContent = `Error cleaning up: ${error.message}`;
    }
}