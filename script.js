document.addEventListener('DOMContentLoaded', () => {
    const list = document.getElementById('projects');
    const count = document.getElementById('work-count');

    // Real screenshots stay in colour over a dot shadow; drawn scenes are pure halftone
    function art(p, plate) {
        const caption = `<figcaption class="plate">Pl. ${plate}</figcaption>`;
        if (p.image) {
            return `
                    <figure class="shot project-art project-shot">
                        <div class="ht" data-scene="shade" data-alt=""></div>
                        <img src="${p.image}" alt="${p.alt}" width="${p.width}" height="${p.height}" loading="lazy">
                        ${caption}
                    </figure>`;
        }
        return `
                    <figure class="ht project-art" data-scene="${p.scene}" data-alt="${p.alt}">
                        ${caption}
                    </figure>`;
    }

    if (list && typeof projectsData !== 'undefined') {
        count.textContent = `${String(projectsData.length).padStart(2, '0')} projects`;
        list.innerHTML = projectsData.map((p, i) => `
            <li class="project">
                <a href="${p.link}" class="project-link">${art(p, String(i + 2).padStart(2, '0'))}
                    <div class="project-meta">
                        <span class="label">${String(i + 1).padStart(2, '0')}</span>
                        <h3>${p.title}</h3>
                        <p>${p.summary}</p>
                        <ul class="tags">${p.tags.map((t) => `<li>${t}</li>`).join('')}</ul>
                    </div>
                </a>
            </li>`).join('');
    }

    Halftone.init();
});
