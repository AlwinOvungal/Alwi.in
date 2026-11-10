import { Bounds, Center, ContactShadows, Grid, OrbitControls } from "@react-three/drei";
import { Canvas, useLoader } from "@react-three/fiber";
import { Suspense, useMemo } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

type Props = {
  bodyUrl?: string;
  baseUrl?: string;
  rawUrl?: string;
  mode: "print" | "raw";
  bodyColor: string;
  baseColor: string;
};

function Part({ url, color }: { url: string; color: string }) {
  const geom = useLoader(STLLoader, url);
  const g = useMemo(() => {
    const c = geom.clone();
    c.computeVertexNormals();
    return c;
  }, [geom]);
  return (
    <mesh geometry={g} castShadow receiveShadow>
      <meshStandardMaterial color={color} roughness={0.92} metalness={0} />
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
        m.material = new THREE.MeshStandardMaterial({ color, roughness: 0.92 });
        m.castShadow = true;
        m.geometry.computeVertexNormals();
      }
    });
    return s;
  }, [gltf, color]);
  return <primitive object={scene} />;
}

export default function Viewer({ bodyUrl, baseUrl, rawUrl, mode, bodyColor, baseColor }: Props) {
  return (
    <Canvas shadows camera={{ position: [70, 60, 90], fov: 35, near: 0.5, far: 4000 }} dpr={[1, 2]}>
      <hemisphereLight args={["#ffffff", "#d9d4c5", 1.1]} />
      <directionalLight position={[60, 120, 80]} intensity={1.6} castShadow shadow-mapSize={[2048, 2048]} />
      <directionalLight position={[-80, 40, -60]} intensity={0.35} />
      <Suspense fallback={null}>
        <Bounds fit clip observe margin={1.5}>
          {mode === "print" && bodyUrl ? (
            // STL files are z-up (printer); three.js is y-up
            <group rotation={[-Math.PI / 2, 0, 0]}>
              <Part url={bodyUrl} color={bodyColor} />
              {baseUrl && <Part url={baseUrl} color={baseColor} />}
            </group>
          ) : rawUrl ? (
            <Center bottom>
              <Raw url={rawUrl} color="#b9b6aa" />
            </Center>
          ) : null}
        </Bounds>
      </Suspense>
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
      <OrbitControls makeDefault enableDamping target={[0, 20, 0]} maxPolarAngle={Math.PI * 0.495} />
    </Canvas>
  );
}
