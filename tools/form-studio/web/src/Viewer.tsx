import { Bounds, Center, ContactShadows, GizmoHelper, GizmoViewport, Grid, OrbitControls, useBounds } from "@react-three/drei";
import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

export type ViewerPart = { url: string; color: string };

type Props = {
  /** the print's parts (each STL in its filament colour) */
  parts: ViewerPart[];
  rawUrl?: string;
  mode: "print" | "raw";
  /** the AI mesh is shown in this colour (the body / first part) */
  rawColor: string;
  /** magnets and keychains lie flat: look down at them */
  flat?: boolean;
};

/** Directions to look FROM (three.js is y-up; the model faces +z). */
const VIEWS = {
  front: { dir: [0, 0, 1], label: "Front", key: "1" },
  back: { dir: [0, 0, -1], label: "Back", key: "" },
  left: { dir: [-1, 0, 0], label: "Left", key: "" },
  right: { dir: [1, 0, 0], label: "Right", key: "3" },
  top: { dir: [0, 1, 0.0001], label: "Top", key: "7" },
  bottom: { dir: [0, -1, 0.0001], label: "Bottom", key: "9" },
  iso: { dir: [0.62, 0.5, 0.78], label: "3/4", key: "0" },
} as const;
type ViewId = keyof typeof VIEWS;

function Part({ url, color }: { url: string; color: string }) {
  const geom = useLoader(STLLoader, url);
  const g = useMemo(() => {
    const c = geom.clone();
    c.computeVertexNormals();
    return c;
  }, [geom]);
  return (
    <mesh geometry={g} castShadow receiveShadow>
      <meshStandardMaterial color={color} roughness={0.92} metalness={0} side={THREE.DoubleSide} />
    </mesh>
  );
}

function Raw({ url, color }: { url: string; color: string }) {
  const gltf = useLoader(GLTFLoader, url);
  const scene = useMemo(() => {
    const s = gltf.scene.clone(true);
    const box = new THREE.Box3().setFromObject(s);
    const h = box.max.y - box.min.y || 1;
    s.scale.setScalar(50 / h); // show the raw mesh at roughly print size
    s.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) {
        const m = o as THREE.Mesh;
        m.material = new THREE.MeshStandardMaterial({ color, roughness: 0.92, side: THREE.DoubleSide });
        m.castShadow = true;
        m.geometry.computeVertexNormals();
      }
    });
    return s;
  }, [gltf, color]);
  return <primitive object={scene} />;
}

/** What's in the view: the print parts in their colours, or the AI mesh. */
function Model({ parts, rawUrl, mode, rawColor }: Pick<Props, "parts" | "rawUrl" | "mode" | "rawColor">) {
  if (mode === "print" && parts.length) {
    return (
      // STL files are z-up (printer); three.js is y-up
      <group rotation={[-Math.PI / 2, 0, 0]}>
        {parts.map((p) => (
          <Part key={p.url} url={p.url} color={p.color} />
        ))}
      </group>
    );
  }
  if (rawUrl) {
    return (
      <Center bottom>
        <Raw url={rawUrl} color={rawColor} />
      </Center>
    );
  }
  return null;
}

/** Turns the camera to a named view of the whole model. Goes through <Bounds>, which animates the camera
 * itself (moving the camera directly would be undone by its own animation). */
function ViewSnap({ view, nonce }: { view: ViewId | null; nonce: number }) {
  const bounds = useBounds();
  useEffect(() => {
    if (!view) return;
    const { center, distance } = bounds.refresh().getSize();
    const dir = new THREE.Vector3(...VIEWS[view].dir).normalize();
    // looking straight down or up, "up" on screen is the model's back / front
    const up: [number, number, number] = view === "top" ? [0, 0, -1] : view === "bottom" ? [0, 0, 1] : [0, 1, 0];
    bounds.moveTo(center.clone().add(dir.multiplyScalar(distance))).lookAt({ target: center, up });
  }, [view, nonce]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** The bed grid fades away when you look from underneath, so it never hides the bottom. */
function BedGrid() {
  const ref = useRef<THREE.Group>(null);
  useFrame(({ camera }) => {
    if (ref.current) ref.current.visible = camera.position.y > -1;
  });
  return (
    <group ref={ref}>
      <ContactShadows position={[0, 0.01, 0]} opacity={0.35} scale={220} blur={2.4} far={60} />
      <Grid
        position={[0, 0, 0]}
        args={[400, 400]}
        cellSize={10}
        cellThickness={0.6}
        cellColor="#d9d5ca"
        sectionSize={50}
        sectionThickness={1}
        sectionColor="#c3beb0"
        fadeDistance={420}
        fadeStrength={1.5}
        infiniteGrid
      />
    </group>
  );
}

function Lights() {
  return (
    <>
      <hemisphereLight args={["#ffffff", "#d9d4c5", 1.1]} />
      {/* The shadow camera must cover the whole object: three's default (a 10 × 10 box) left a square
          of self-shadow speckle on prints — the "diamond" patch. Bias settings keep surfaces clean. */}
      <directionalLight
        position={[60, 120, 80]}
        intensity={1.6}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-130}
        shadow-camera-right={130}
        shadow-camera-top={130}
        shadow-camera-bottom={-130}
        shadow-camera-near={1}
        shadow-camera-far={500}
        shadow-bias={-0.0004}
        shadow-normalBias={0.35}
      />
      <directionalLight position={[-80, 40, -60]} intensity={0.35} />
      <directionalLight position={[0, -120, 0]} intensity={0.45} />
    </>
  );
}

const TRI_VIEWS: { id: ViewId; label: string }[] = [
  { id: "front", label: "Front" },
  { id: "right", label: "Side" },
  { id: "top", label: "Top" },
];

/** One fixed view for the 3-view layout: the camera starts on its axis and <Bounds> keeps the model
 * fitted, so every panel updates together when the shape or a colour changes. */
function TriPanel({ view, children }: { view: ViewId; children: React.ReactNode }) {
  const dir = new THREE.Vector3(...VIEWS[view].dir).normalize().multiplyScalar(160);
  return (
    <Canvas shadows camera={{ position: [dir.x, dir.y, dir.z], up: view === "top" ? [0, 0, -1] : [0, 1, 0], fov: 30, near: 0.5, far: 4000 }} dpr={[1, 2]}>
      <Lights />
      <Suspense fallback={null}>
        <Bounds fit clip observe margin={1.35}>
          {children}
        </Bounds>
      </Suspense>
    </Canvas>
  );
}

export default function Viewer({ parts, rawUrl, mode, rawColor, flat }: Props) {
  const lookDown = flat && mode === "print";
  const [view, setView] = useState<{ id: ViewId | null; n: number }>({ id: null, n: 0 });
  const [spin, setSpin] = useState(false);
  const [tri, setTri] = useState(false);
  const snap = (id: ViewId) => setView((v) => ({ id, n: v.n + 1 }));
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "r" || e.key === "R") return setSpin((s) => !s);
    const hit = (Object.keys(VIEWS) as ViewId[]).find((k) => VIEWS[k].key && VIEWS[k].key === e.key);
    if (hit) (setTri(false), snap(hit));
  };
  const model = <Model parts={parts} rawUrl={rawUrl} mode={mode} rawColor={rawColor} />;

  return (
    <div className="viewer-wrap" tabIndex={0} onKeyDown={onKey} aria-label="3D view: drag to turn, scroll to zoom. Keys: 1 front, 3 right, 7 top, 9 bottom, 0 three-quarter, R spin">
      {tri ? (
        <div className="tri-grid" aria-label="Front, side and top views">
          {TRI_VIEWS.map((v) => (
            <figure key={v.id} className="tri-panel">
              <TriPanel view={v.id}>{model}</TriPanel>
              <figcaption className="chip">{v.label}</figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <Canvas
          key={lookDown ? "flat" : "standing"}
          shadows
          camera={{ position: lookDown ? [0, 160, 45] : [70, 60, 90], fov: 35, near: 0.5, far: 4000 }}
          dpr={[1, 2]}
        >
          <Lights />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.5}>
              {model}
              <ViewSnap view={view.id} nonce={view.n} />
            </Bounds>
          </Suspense>
          <BedGrid />
          {/* full turn in every direction, including underneath (to check a magnet pocket) */}
          <OrbitControls makeDefault enableDamping target={[0, lookDown ? 0 : 20, 0]} autoRotate={spin} autoRotateSpeed={2.5} />
          <GizmoHelper alignment="bottom-right" margin={[64, 64]}>
            <GizmoViewport axisColors={["#c0453a", "#5e8a2e", "#3d6fb6"]} labelColor="#ffffff" />
          </GizmoHelper>
        </Canvas>
      )}
      <div className="view-tools" role="toolbar" aria-label="Views">
        <button type="button" className={tri ? "on" : ""} onClick={() => setTri((t) => !t)} title="Front, side and top at once">
          3 views
        </button>
        <span className="sep" aria-hidden />
        {(Object.keys(VIEWS) as ViewId[]).map((k) => (
          <button key={k} type="button" className={!tri && view.id === k ? "on" : ""} disabled={tri}
            onClick={() => snap(k)} title={VIEWS[k].key ? `${VIEWS[k].label} (key ${VIEWS[k].key})` : VIEWS[k].label}>
            {VIEWS[k].label}
          </button>
        ))}
        <button type="button" className={spin ? "on" : ""} disabled={tri} onClick={() => setSpin((s) => !s)} title="Turn slowly (key R)">Spin</button>
      </div>
    </div>
  );
}
