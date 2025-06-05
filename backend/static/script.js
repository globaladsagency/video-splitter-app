document.addEventListener('DOMContentLoaded', () => {
    const navLinks = document.querySelectorAll('nav a');
    const sections = document.querySelectorAll('main section');

    // Aquí iría tu código existente para la navegación si lo tienes
    // Por ejemplo:
    navLinks.forEach(link => {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            const targetId = link.getAttribute('href').substring(1); // Obtener 'dividir-historias'
            sections.forEach(section => {
                if (section.id === targetId) {
                    section.classList.add('active-section');
                } else {
                    section.classList.remove('active-section');
                }
            });
            navLinks.forEach(navLink => navLink.classList.remove('active'));
            link.classList.add('active');
        });
    });


    // --- Funcionalidad de Dividir Historias ---
    const videoInput = document.getElementById('videoInput');
    const segmentDurationInput = document.getElementById('segmentDuration');
    const splitButton = document.getElementById('splitButton');
    const downloadArea = document.getElementById('downloadArea');
    const downloadMessage = document.getElementById('downloadMessage');
    const downloadAllButton = document.getElementById('downloadAllButton');
    const downloadAllIndividualButton = document.getElementById('downloadAllIndividual'); // Nuevo botón

    let uploadedVideoFile = null;
    let fragmentFilenames = [];
    let fragmentURLs = []; // Para almacenar las URLs de descarga

    videoInput.addEventListener('change', (event) => {
        uploadedVideoFile = event.target.files[0];
        splitButton.disabled = !uploadedVideoFile;
        downloadAllButton.disabled = true;
        downloadAllIndividualButton.disabled = true; // Deshabilitar también este botón
        downloadMessage.textContent = '';
    });

    splitButton.addEventListener('click', async () => {
        if (!uploadedVideoFile) {
            alert('Por favor, selecciona un video primero.');
            return;
        }

        const segmentDuration = parseInt(segmentDurationInput.value, 10);
        if (isNaN(segmentDuration) || segmentDuration <= 0) {
            alert('Por favor, introduce una duración de fragmento válida.');
            return;
        }

        const formData = new FormData();
        formData.append('video', uploadedVideoFile);
        formData.append('segment_duration', segmentDuration);

        try {
            // ¡IMPORTANTE!: CAMBIO A URL RELATIVA PARA EL DESPLIEGUE
            // La URL relativa '/api/split_video' apuntará al mismo host donde se sirve el frontend
            const response = await fetch('/api/split_video', { 
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                const data = await response.json();
                fragmentFilenames = data.fragment_filenames || [];
                fragmentURLs = data.fragment_urls || []; // Guardar las URLs
                downloadMessage.textContent = `${fragmentFilenames.length} fragmentos generados.`;
                downloadAllButton.disabled = fragmentFilenames.length === 0;
                downloadAllIndividualButton.disabled = fragmentFilenames.length === 0; // Habilitar el nuevo botón
            } else {
                const errorData = await response.json();
                alert(`Error al dividir el video: ${errorData.error || 'Error desconocido'}`);
                downloadAllButton.disabled = true;
                downloadAllIndividualButton.disabled = true;
                downloadMessage.textContent = 'Error al generar los fragmentos.';
            }

        } catch (error) {
            console.error('Error de red:', error);
            alert('Ocurrió un error al comunicarse con el servidor.');
            downloadAllButton.disabled = true;
            downloadAllIndividualButton.disabled = true;
            downloadMessage.textContent = 'Error de conexión con el servidor.';
        }
    });

    // --- Funcionalidad de Descargar Todos (ZIP) ---
    downloadAllButton.addEventListener('click', async () => {
        if (fragmentFilenames.length > 0) {
            try {
                // ¡IMPORTANTE!: CAMBIO A URL RELATIVA PARA EL DESPLIEGUE
                // La URL relativa '/api/download_all' apuntará al mismo host donde se sirve el frontend
                const response = await fetch('/api/download_all', { 
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ filenames: fragmentFilenames }),
                });

                if (response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'fragmentos.zip';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                } else {
                    const errorData = await response.json();
                    alert(`Error al descargar todos los fragmentos (ZIP): ${errorData.error || 'Error desconocido'}`);
                }

            } catch (error) {
                console.error('Error al descargar todos (ZIP):', error);
                alert('Ocurrió un error al descargar todos los fragmentos (ZIP).');
            }
        } else {
            alert('No hay fragmentos para descargar.');
        }
    });

    // --- Funcionalidad de Descargar Todos (Individual) (Secuencial Asíncrono) ---
    downloadAllIndividualButton.addEventListener('click', async () => {
        if (fragmentURLs.length > 0) {
            alert('Iniciando descarga individual de fragmentos. Por favor, espere...');
            for (const url of fragmentURLs) {
                const a = document.createElement('a');
                a.href = url;
                a.download = url.substring(url.lastIndexOf('/') + 1);
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                await new Promise(resolve => setTimeout(resolve, 1000)); // Espera 1 segundo entre descargas
            }
            alert('Descarga individual de todos los fragmentos completada.');
        } else {
            alert('No hay fragmentos para descargar.');
        }
    });
});