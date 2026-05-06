document.addEventListener('DOMContentLoaded', () => {
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

});
