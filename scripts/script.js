// No imports needed, uses window.portfolioProjects

document.addEventListener('DOMContentLoaded', () => {
    console.log("Portfolio Initializing...");
    initTheme();

    const path = window.location.pathname;
    console.log("Current Path:", path);

    if (path.includes('details.html')) {
        renderProjectDetails();
    } else {
        renderProjectList();
    }
    
    // Initialize animations AFTER rendering dynamic content
    setTimeout(initAnimations, 100);
});

// --- Theme Logic ---
function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    
    const toggleBtn = document.getElementById('theme-toggle');
    if (toggleBtn) {
        toggleBtn.textContent = savedTheme === 'light' ? 'Stealth' : 'Raw';
        toggleBtn.addEventListener('click', () => {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            toggleBtn.textContent = newTheme === 'light' ? 'Stealth' : 'Raw';
        });
    }
}

// --- Animation Logic ---
function initAnimations() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, { threshold: 0.1 });

    document.querySelectorAll('.fade-in').forEach(el => observer.observe(el));
}

// --- Render Project Listing ---
function renderProjectList() {
    const grid = document.getElementById('project-grid');
    if (!grid) return;

    const projects = window.portfolioProjects;
    if (!projects || !Array.isArray(projects)) {
        console.error("Portfolio data not found. Ensure projects.js is loaded correctly.");
        return;
    }

    grid.innerHTML = projects.map(p => `
        <div class="project-card fade-in" onclick="window.location.href='details.html?id=${p.id}'">
            <img src="${p.thumbnail}" alt="${p.title}" loading="lazy">
            <div class="project-info">
                <div style="display: flex; justify-content: space-between; align-items: baseline;">
                    <h3>${p.title}</h3>
                    <span>${p.year}</span>
                </div>
                <p>${p.description}</p>
                <div class="project-tags">
                    ${p.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                </div>
            </div>
        </div>
    `).join('');
}

// --- Render Project Details ---
function renderProjectDetails() {
    const params = new URLSearchParams(window.location.search);
    const projectId = params.get('id');
    const projects = window.portfolioProjects;
    const project = projects.find(p => p.id === projectId);

    if (!project) {
        window.location.href = 'index.html';
        return;
    }

    // Update Titles
    document.title = `${project.title} | Alwi Portfolio`;
    document.getElementById('project-title').textContent = project.title;
    document.getElementById('project-category').textContent = project.category;
    document.getElementById('project-year').textContent = project.year;
    document.getElementById('project-full-desc').textContent = project.fullDescription;

    // Render Images
    const imageContainer = document.getElementById('project-images');
    imageContainer.innerHTML = project.images.map(img => `
        <img src="${img}" alt="${project.title}" class="fade-in" style="width: 100%; margin-bottom: 2rem; border: var(--border);">
    `).join('');

    // Ensure animations work for new content
    setTimeout(initAnimations, 100);
}
