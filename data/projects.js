// Each project gets one halftone plate. Use `image` for a real screenshot or photo
// (it is converted to dots automatically), or `scene` for a drawn illustration.
// Optional: `focus` "x y" crop focus (0..1), `fit` "contain" to show the whole image,
// `cell` a finer dot pitch for UI screenshots.
const projectsData = [
    {
        id: "display-monk",
        title: "Display Monk",
        summary: "A free, open-source macOS menu bar app for every display setting macOS hides: synced brightness down to black, sharp HiDPI on any monitor, arrangement and monitor controls, in one quiet menu.",
        tags: ["macOS app", "Open source", "2026"],
        image: "assets/work/display-monk/menu.jpg",
        fit: "contain",
        cell: 3,
        alt: "The Display Monk menu, with brightness sliders for a built-in and an external display",
        link: "work/display-monk"
    },
    {
        id: "alwi-in",
        title: "Printed with Dots",
        summary: "This site. A halftone renderer that turns any photo, screenshot or drawing into one consistent ink-dot print, with a style guide and a lab to preview your own images.",
        tags: ["Web", "Generative", "2026"],
        scene: "orb",
        alt: "Halftone illustration of two shaded spheres resting on a surface",
        link: "styleguide"
    }
];
