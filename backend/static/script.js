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
        // const fragmentList = document.getElementById('fragmentList'); // Ya no usaremos este para los enlaces directos
        const progressContainer = document.getElementById('progressContainer');
        const progressMessage = document.getElementById('progressMessage');
        const progressBar = document.getElementById('progressBar');
        const overallProgressText = document.getElementById('overallProgressText');
        const downloadArea = document.getElementById('downloadArea'); 
        const downloadButtonsContainer = document.getElementById('downloadButtons'); 
        const downloadMessage = document.getElementById('downloadMessage'); 
        const fragmentPreviewsContainer = document.getElementById('fragmentPreviews'); // NUEVO: Contenedor de vistas previas

        // Resetear UI al inicio de un nuevo procesamiento
        statusMessage.textContent = 'Iniciando procesamiento...';
        statusMessage.className = 'message info';
        // fragmentList.innerHTML = ''; // Limpiar lista de fragmentos anteriores (si la usabas)
        fragmentPreviewsContainer.innerHTML = ''; // Limpiar vistas previas anteriores
        downloadButtonsContainer.innerHTML = ''; // Limpiar botones de descarga anteriores (se recrearán)
        splitButton.disabled = true; // Deshabilitar botón de dividir
        downloadArea.style.display = 'none'; // Ocultar área de descarga
        downloadButtonsContainer.style.display = 'none'; // Ocultar contenedor de botones
        downloadMessage.style.display = 'none'; // Ocultar mensaje de descarga

        // Mostrar contenedor de progreso e inicializar
        progressContainer.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = '0%';
        overallProgressText.textContent = '0% Completado';
        progressMessage.textContent = 'Preparando...';

        try {
            const response = await fetch('/api/split_video', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                statusMessage.textContent = `Error al iniciar el procesamiento: ${errorData.error || 'Desconocido'}`;
                statusMessage.className = 'message error';
                progressContainer.style.display = 'none'; // Ocultar barra de progreso en caso de error inicial
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
                buffer = events.pop(); // Guarda la última parte incompleta

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
                            overallProgressText.textContent = `${percentage.toFixed(0)}% Completado`;
                            
                            if (percentage >= 100) {
                                statusMessage.textContent = 'Proceso completado. Obteniendo fragmentos...';
                                statusMessage.className = 'message success';
                            }
                        } else if (data.startsWith('error:')) {
                            statusMessage.textContent = `Error durante el procesamiento: ${data.substring(6).trim()}`;
                            statusMessage.className = 'message error';
                            progressContainer.style.display = 'none';
                            reader.cancel(); 
                            break; 
                        } else if (data.startsWith('fragments:')) {
                            const fragmentsJsonString = data.substring(10).trim();
                            try {
                                const fragments = JSON.parse(fragmentsJsonString);
                                if (fragments && fragments.length > 0) {
                                    // fragmentList.innerHTML = ''; // Ya no usamos esto
                                    fragmentPreviewsContainer.innerHTML = ''; // Limpiar por si acaso
                                    downloadArea.style.display = 'block'; 
                                    downloadMessage.style.display = 'block'; 

                                    // Crear las vistas previas de los fragmentos en la matriz
                                    fragments.forEach(url => {
                                        const fragmentItem = document.createElement('div');
                                        fragmentItem.className = 'fragment-item';

                                        const videoElement = document.createElement('video');
                                        videoElement.src = url;
                                        videoElement.controls = true; // Permite reproducir
                                        videoElement.preload = 'metadata'; // Carga solo metadatos para vista previa
                                        videoElement.style.maxWidth = '100%';
                                        videoElement.style.height = 'auto';
                                        
                                        // Opcional: Si quieres un thumbnail en lugar del video completo,
                                        // podrías pedir a FFmpeg que genere uno y usar <img> aquí.
                                        // Por ahora, usamos el propio <video> con controls.

                                        const downloadLink = document.createElement('a');
                                        downloadLink.href = url;
                                        downloadLink.textContent = url.split('/').pop(); // Muestra solo el nombre del archivo
                                        downloadLink.download = url.split('/').pop(); // Para la descarga

                                        fragmentItem.appendChild(videoElement);
                                        fragmentItem.appendChild(downloadLink);
                                        fragmentPreviewsContainer.appendChild(fragmentItem);
                                    });


                                    // Crear y añadir el botón de Descargar Todos (ZIP)
                                    const downloadAllZipBtn = document.createElement('button');
                                    downloadAllZipBtn.id = 'downloadAllButton'; 
                                    downloadAllZipBtn.textContent = 'Descargar Todos (ZIP)';
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
                                    
                                    downloadButtonsContainer.style.display = 'flex'; // Mostrar el contenedor de botones
                                    
                                } else {
                                    statusMessage.textContent = 'Proceso completado, pero no se generaron fragmentos.';
                                    statusMessage.className = 'message info';
                                }
                            } catch (e) {
                                console.error("Error parsing fragments JSON:", e, fragmentsJsonString);
                                statusMessage.textContent = "Error al procesar la lista de fragmentos.";
                                statusMessage.className = 'message error';
                            }
                        }
                    }
                }
            }
            
        } catch (error) {
            console.error('Error durante la carga o el streaming:', error);
            statusMessage.textContent = 'Error de red o del servidor. Inténtalo de nuevo.';
            statusMessage.className = 'message error';
        } finally {
            splitButton.disabled = false;
        }
    });
});