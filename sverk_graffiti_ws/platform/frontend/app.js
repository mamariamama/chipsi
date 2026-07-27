function startDebate() {
    const prompt = document.getElementById('prompt-input').value;
    fetch('/api/start-debate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt})
    });
    // установить iframe VLM
    document.getElementById('vlm-frame').src = VLM_CAMERA_URL; // из переменной окружения или вшито
}
