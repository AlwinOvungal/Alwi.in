document.addEventListener('DOMContentLoaded', () => {
    // Dynamic Project Loading (Pseudo-CMS)
    const projectsContainer = document.getElementById('projects-container');
    const projectCountElement = document.querySelector('.section-header .count');

    function renderProjects() {
        if (!projectsContainer || !projectsData) return;
        
        // Update project count
        if (projectCountElement) {
            projectCountElement.textContent = projectsData.length.toString().padStart(2, '0');
        }

        projectsContainer.innerHTML = projectsData.map(project => `
            <div class="project-card" data-cursor="view">
                <div class="project-image">
                    <img src="${project.image}" alt="${project.alt}" loading="lazy" width="1024" height="1024">
                </div>
                <div class="project-meta">
                    <h3>${project.title}</h3>
                    <div class="tags">
                        ${project.tags.map(tag => `<span>${tag}</span>`).join('')}
                    </div>
                </div>
            </div>
        `).join('');

        // Re-attach cursor hover events to new elements
        const newInteractables = projectsContainer.querySelectorAll('[data-cursor]');
        newInteractables.forEach(el => {
            el.addEventListener('mouseenter', () => {
                const type = el.getAttribute('data-cursor');
                document.body.classList.add(`cursor-${type}`);
            });
            
            el.addEventListener('mouseleave', () => {
                const type = el.getAttribute('data-cursor');
                document.body.classList.remove(`cursor-${type}`);
            });
        });
    }

    renderProjects();

    // Custom Cursor Logic
    const cursorDot = document.getElementById('cursor-dot');
    const cursorOutline = document.getElementById('cursor-outline');
    
    // Check if device supports hover (not a touch device)
    const isTouchDevice = (('ontouchstart' in window) || (navigator.maxTouchPoints > 0));
    
    if (!isTouchDevice) {
        window.addEventListener('mousemove', (e) => {
            const posX = e.clientX;
            const posY = e.clientY;
            
            // Move dot instantly
            cursorDot.style.left = `${posX}px`;
            cursorDot.style.top = `${posY}px`;
            
            // Move outline with slight delay for smooth effect
            cursorOutline.animate({
                left: `${posX}px`,
                top: `${posY}px`
            }, { duration: 150, fill: "forwards" });
        });

        // Add hover effects based on data attributes
        const interactables = document.querySelectorAll('[data-cursor]');
        
        interactables.forEach(el => {
            el.addEventListener('mouseenter', () => {
                const type = el.getAttribute('data-cursor');
                document.body.classList.add(`cursor-${type}`);
            });
            
            el.addEventListener('mouseleave', () => {
                const type = el.getAttribute('data-cursor');
                document.body.classList.remove(`cursor-${type}`);
            });
        });
    } else {
        // Hide cursors on touch devices
        cursorDot.style.display = 'none';
        cursorOutline.style.display = 'none';
    }

    // Horizontal Scroll Drag Logic
    const scrollWrapper = document.getElementById('scroll-wrapper');
    let isDown = false;
    let startX;
    let scrollLeft;

    scrollWrapper.addEventListener('mousedown', (e) => {
        isDown = true;
        startX = e.pageX - scrollWrapper.offsetLeft;
        scrollLeft = scrollWrapper.scrollLeft;
    });

    scrollWrapper.addEventListener('mouseleave', () => {
        isDown = false;
    });

    scrollWrapper.addEventListener('mouseup', () => {
        isDown = false;
    });

    scrollWrapper.addEventListener('mousemove', (e) => {
        if (!isDown) return;
        e.preventDefault();
        const x = e.pageX - scrollWrapper.offsetLeft;
        const walk = (x - startX) * 2; // Scroll-fast multiplier
        scrollWrapper.scrollLeft = scrollLeft - walk;
    });

    // --- Animation Logic (Motion Library) ---
    try {
        const { animate, stagger, inView } = motion;

        // 1. Hero Entrance Animation
        animate(".huge-title", 
            { opacity: [0, 1], y: [100, 0] }, 
            { duration: 1.2, easing: [0.22, 1, 0.36, 1] }
        );

        animate(".hero-subtitle", 
            { opacity: [0, 1], y: [30, 0] }, 
            { delay: 0.4, duration: 0.8 }
        );

        animate(".hero .btn", 
            { opacity: [0, 1], scale: [0.9, 1] }, 
            { delay: 0.6, duration: 0.5 }
        );

        animate(".hero-image img", 
            { opacity: [0, 1], scale: [1.1, 1], x: [50, 0] }, 
            { delay: 0.2, duration: 1.5, easing: [0.22, 1, 0.36, 1] }
        );

        // 2. Scroll Reveal Animations
        // Reveal section headers
        inView(".section-header", ({ target }) => {
            animate(target, { opacity: [0, 1], y: [50, 0] }, { duration: 0.8 });
        });

        // Staggered reveal for project cards
        inView(".projects-container", ({ target }) => {
            animate(".project-card", 
                { opacity: [0, 1], y: [100, 0] }, 
                { delay: stagger(0.1), duration: 0.8, easing: [0.22, 1, 0.36, 1] }
            );
        });

        // About section reveal
        inView(".about-grid", ({ target }) => {
            animate(".about-image-container", { opacity: [0, 1], x: [-50, 0] }, { duration: 1 });
            animate(".about-content", { opacity: [0, 1], x: [50, 0] }, { duration: 1, delay: 0.2 });
        });
    } catch (error) {
        console.error("Motion animations failed to load:", error);
        // Fallback: Make everything visible if animations fail
        const hiddenElements = document.querySelectorAll('.huge-title, .hero-subtitle, .hero-image img, .project-card, .section-header');
        hiddenElements.forEach(el => el.style.opacity = "1");
    }

});
