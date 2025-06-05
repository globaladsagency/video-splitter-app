document.addEventListener('DOMContentLoaded', () => {
    const uploadForm = document.getElementById('uploadForm');
    if (!uploadForm) {
        console.error("El formulario de carga no se encontró. Asegúrate de que index.html esté cargado.");
        return;
    }

    uploadForm.addEventListener('submit', async function(event) {
        event.preventDefault();

        const form = event.target;
        const formData = new FormData(form);
        const splitButton = document.getElementById('splitButton');
        const statusMessage = document.getElementById('statusMessage');
        const progressContainer = document.getElementById('progressContainer');
        const progressMessage = document.getElementById('progressMessage');
        const progressBar = document.getElementById('progressBar');
        const overallProgressText = document.getElementById('overallProgressText');
        const downloadArea = document.getElementById('downloadArea'); 
        const downloadButtonsContainer = document.getElementById('downloadButtons'); 
        const downloadMessage = document.getElementById('downloadMessage'); 
        const fragmentPreviewsContainer = document.getElementById('fragmentPreviews');

        // Resetear UI al inicio de un nuevo procesamiento
        statusMessage.textContent = 'Iniciando procesamiento...';
        statusMessage.className = 'alert alert-info'; // Clases de Bootstrap para alertas
        fragmentPreviewsContainer.innerHTML = ''; 
        downloadButtonsContainer.innerHTML = ''; 
        splitButton.disabled = true; 
        downloadArea.style.display = 'none'; 
        downloadMessage.style.display = 'none'; 

        // Mostrar contenedor de progreso e inicializar
        progressContainer.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = '0%';
        overallProgressText.textContent = '0% Completado';
        progressMessage.textContent = 'Preparando...';

        // Asegurarse de que la barra de progreso de Bootstrap está lista
        progressBar.setAttribute('aria-valuenow', '0');


        try {
            const response = await fetch('/api/split_video', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                statusMessage.textContent = `Error al iniciar el procesamiento: ${errorData.error || 'Desconocido'}`;
                statusMessage.className = 'alert alert-danger'; // Clases de Bootstrap para alertas
                progressContainer.style.display = 'none';
                splitButton.disabled = false;
                return;
            }
            
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    console.log("Stream complete");
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                const events = buffer.split('\n\n');
                buffer = events.pop();

                for (const eventString of events) {
                    if (eventString.startsWith('data:')) {
                        let data = eventString.substring(5).trim();
                        
                        if (data.startsWith('message:')) {
                            progressMessage.textContent = data.substring(8).trim();
                        } else if (data.startsWith('overall_progress:')) {
                            let percentage = parseFloat(data.substring(17).trim());
                            percentage = Math.max(0, Math.min(100, percentage)); 
                            progressBar.style.width = `${percentage}%`;
                            progressBar.textContent = `${percentage.toFixed(0)}%`; 
                            progressBar.setAttribute('aria-valuenow', percentage.toFixed(0)); // Actualiza el atributo aria-valuenow
                            overallProgressText.textContent = `${percentage.toFixed(0)}% Completado`;
                            
                            if (percentage >= 100) {
                                statusMessage.textContent = 'Proceso completado. Obteniendo fragmentos...';
                                statusMessage.className = 'alert alert-success'; // Clases de Bootstrap para alertas
                            }
                        } else if (data.startsWith('error:')) {
                            statusMessage.textContent = `Error durante el procesamiento: ${data.substring(6).trim()}`;
                            statusMessage.className = 'alert alert-danger'; // Clases de Bootstrap para alertas
                            progressContainer.style.display = 'none';
                            reader.cancel(); 
                            break; 
                        } else if (data.startsWith('fragments:')) {
                            const fragmentsJsonString = data.substring(10).trim();
                            try {
                                const fragments = JSON.parse(fragmentsJsonString);
                                if (fragments && fragments.length > 0) {
                                    fragmentPreviewsContainer.innerHTML = ''; 
                                    downloadArea.style.display = 'block'; 
                                    downloadMessage.style.display = 'block'; 

                                    // Crear las vistas previas de los fragmentos en la matriz
                                    fragments.forEach((url, index) => { // Asegúrate de incluir 'index' aquí
                                        const fragmentItem = document.createElement('div');
                                        fragmentItem.className = 'col'; // Clase de Bootstrap para columna

                                        const cardDiv = document.createElement('div');
                                        cardDiv.className = 'card h-100'; // Clases de Bootstrap para la tarjeta

                                        const videoElement = document.createElement('video');
                                        videoElement.src = url;
                                        videoElement.controls = true; 
                                        videoElement.preload = 'metadata'; 
                                        videoElement.className = 'card-img-top'; // Clase de Bootstrap para imágenes/videos en cards
                                        videoElement.style.maxWidth = '100%';
                                        videoElement.style.height = 'auto';
                                        
                                        const cardBodyDiv = document.createElement('div');
                                        cardBodyDiv.className = 'card-body d-flex flex-column justify-content-between'; // Clases de Bootstrap

                                        // --- Lógica para el nombre del fragmento ---
                                        const partNumber = index + 1; // Para que empiece en 1, no en 0
                                        const displayFileName = `Parte ${partNumber}`; // Texto para mostrar al usuario (ej. "Parte 1")
                                        const actualFileName = url.split('/').pop(); // Nombre original para la descarga (ej. "fragment_1.mp4")
                                        // ------------------------------------------

                                        const downloadLink = document.createElement('a');
                                        downloadLink.href = url;
                                        downloadLink.textContent = displayFileName; // Usa el texto amigable
                                        downloadLink.download = actualFileName; // Mantiene el nombre original para la descarga
                                        downloadLink.className = 'btn btn-sm btn-outline-secondary mt-auto'; // Clases de Bootstrap para el botón de descarga

                                        cardBodyDiv.appendChild(downloadLink);
                                        cardDiv.appendChild(videoElement);
                                        cardDiv.appendChild(cardBodyDiv);
                                        fragmentItem.appendChild(cardDiv);
                                        fragmentPreviewsContainer.appendChild(fragmentItem);
                                    });


                                    // Crear y añadir el botón de Descargar Todos (ZIP)
                                    const downloadAllZipBtn = document.createElement('button');
                                    downloadAllZipBtn.id = 'downloadAllButton'; 
                                    downloadAllZipBtn.textContent = 'Descargar Todos (ZIP)';
                                    downloadAllZipBtn.className = 'btn btn-primary'; // Clases de Bootstrap para botones
                                    downloadAllZipBtn.addEventListener('click', async () => {
                                        const filenames = fragments.map(url => url.split('/').pop());
                                        try {
                                            const zipResponse = await fetch('/api/download_all', {
                                                method: 'POST',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ filenames: filenames })
                                            });
                                            if (zipResponse.ok) {
                                                const blob = await zipResponse.blob();
                                                const url = window.URL.createObjectURL(blob);
                                                const a = document.createElement('a');
                                                a.style.display = 'none';
                                                a.href = url;
                                                a.download = 'fragmentos.zip';
                                                document.body.appendChild(a);
                                                a.click();
                                                window.URL.revokeObjectURL(url);
                                            } else {
                                                const zipError = await zipResponse.json();
                                                alert(`Error al descargar ZIP: ${zipError.error || 'Desconocido'}`);
                                            }
                                        } catch (zipFetchError) {
                                            console.error('Error al solicitar ZIP:', zipFetchError);
                                            alert('Error de red al intentar descargar el ZIP.');
                                        }
                                    });
                                    downloadButtonsContainer.appendChild(downloadAllZipBtn);

                                    // Crear y añadir el botón para descargar todos individualmente
                                    const downloadAllIndividualBtn = document.createElement('button');
                                    downloadAllIndividualBtn.id = 'downloadAllIndividual'; 
                                    downloadAllIndividualBtn.textContent = 'Descargar Todos Individualmente';
                                    downloadAllIndividualBtn.className = 'btn btn-secondary'; // Clases de Bootstrap para botones
                                    downloadAllIndividualBtn.addEventListener('click', () => {
                                        alert('La descarga de fragmentos individuales ha comenzado. Revisa tu carpeta de descargas.');
                                        fragments.forEach(url => {
                                            const a = document.createElement('a');
                                            a.href = url;
                                            a.download = url.split('/').pop(); 
                                            document.body.appendChild(a); 
                                            a.click(); 
                                            document.body.removeChild(a); 
                                        });
                                    });
                                    downloadButtonsContainer.appendChild(downloadAllIndividualBtn);
                                    
                                } else {
                                    statusMessage.textContent = 'Proceso completado, pero no se generaron fragmentos.';
                                    statusMessage.className = 'alert alert-info'; // Clases de Bootstrap para alertas
                                }
                            } catch (e) {
                                console.error("Error parsing fragments JSON:", e, fragmentsJsonString);
                                statusMessage.textContent = "Error al procesar la lista de fragmentos.";
                                statusMessage.className = 'alert alert-danger'; // Clases de Bootstrap para alertas
                            }
                        }
                    }
                }
            }
            
        } catch (error) {
            console.error('Error durante la carga o el streaming:', error);
            statusMessage.textContent = 'Error de red o del servidor. Inténtalo de nuevo.';
            statusMessage.className = 'alert alert-danger'; // Clases de Bootstrap para alertas
        } finally {
            splitButton.disabled = false;
        }
    });
});