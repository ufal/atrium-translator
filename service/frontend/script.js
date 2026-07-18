/**
 * Minimal client logic for the ATRIUM Translator demo frontend.
 * Uploads the selected XML to POST /translate and offers the translated
 * XML attachment as a download link.
 */
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('translateForm');
    const loader = document.getElementById('loader');
    const resultDiv = document.getElementById('results');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        resultDiv.innerHTML = '';
        loader.style.display = 'block';

        const fileInput = document.getElementById('fileInput');
        const file = fileInput.files[0];
        const sourceLang = document.getElementById('sourceLang').value;
        const targetLang = document.getElementById('targetLang').value;
        const isAlto = document.getElementById('isAlto').value;

        const formData = new FormData();
        formData.append('file', file);

        const baseUrl = window.location.origin.includes('localhost') ? 'http://localhost:8000' : '';
        const query = `source_lang=${encodeURIComponent(sourceLang)}` +
                      `&target_lang=${encodeURIComponent(targetLang)}` +
                      `&is_alto=${encodeURIComponent(isAlto)}`;

        try {
            const response = await fetch(`${baseUrl}/translate?${query}`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                let detail = `Server error: ${response.status} ${response.statusText}`;
                try {
                    const errJson = await response.json();
                    if (errJson.detail) detail = errJson.detail;
                } catch (_) { /* non-JSON error body */ }
                throw new Error(detail);
            }

            // The endpoint returns the translated XML as an attachment.
            const blob = await response.blob();
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="?([^";]+)"?/);
            const outName = match ? match[1] : `translated_${targetLang}.xml`;

            const url = URL.createObjectURL(blob);
            resultDiv.innerHTML = `
                <div class="result-card">
                    ✅ Translation finished:
                    <a href="${url}" download="${outName}">${outName}</a>
                    (${(blob.size / 1024).toFixed(1)} kB)
                </div>`;
        } catch (err) {
            console.error(err);
            resultDiv.innerHTML = `<div class="result-card error"><strong>Error:</strong> ${err.message}</div>`;
        } finally {
            loader.style.display = 'none';
        }
    });
});
