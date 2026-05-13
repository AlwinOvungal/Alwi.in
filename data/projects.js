const projectsData = [
    {
        id: "finova-banking",
        title: "Finova Banking",
        tags: ["Fintech", "2026"],
        image: "assets/project-1.png",
        alt: "Finova Banking - Fintech App Interface",
        link: "#"
    },
    {
        id: "synergy-analytics",
        title: "Synergy Analytics",
        tags: ["SaaS", "2025"],
        image: "assets/project-2.png",
        alt: "Synergy Analytics - SaaS Dashboard Design",
        link: "#"
    },
    {
        id: "lumina-home",
        title: "Lumina Home",
        tags: ["IoT", "2025"],
        image: "assets/project-3.png",
        alt: "Lumina Home - Smart Home IoT Mobile App",
        link: "#"
    }
];

// Export for potential future use in modules, 
// though we'll use it as a global for simple HTML/JS setup
if (typeof module !== 'undefined' && module.exports) {
    module.exports = projectsData;
}
