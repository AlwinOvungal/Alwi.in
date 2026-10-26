document.addEventListener('DOMContentLoaded', () => {
    const list = document.getElementById('projects');
    const count = document.getElementById('work-count');

    if (list && typeof projectsData !== 'undefined') {
        count.textContent = `${String(projectsData.length).padStart(2, '0')} projects`;
        list.innerHTML = projectsData.map((p, i) => {
            const source = p.image ? `data-src="${p.image}"` : `data-scene="${p.scene}"`;
            const plate = String(i + 2).padStart(2, '0');
            return `
            <li class="project">
                <a href="${p.link}" class="project-link">
                    <figure class="ht project-art" ${source} data-alt="${p.alt}">
                        <figcaption class="plate">Pl. ${plate}</figcaption>
                    </figure>
                    <div class="project-meta">
                        <span class="label">${String(i + 1).padStart(2, '0')}</span>
                        <h3>${p.title}</h3>
                        <p>${p.summary}</p>
                        <ul class="tags">${p.tags.map((t) => `<li>${t}</li>`).join('')}</ul>
                    </div>
                </a>
            </li>`;
        }).join('');
    }

    Halftone.init();
});
