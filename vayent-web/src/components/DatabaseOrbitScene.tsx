import React, { useEffect, useRef } from "react";
import * as THREE from "three";

type DatabaseOrbitSceneProps = {
  className?: string;
  compact?: boolean;
};

const DatabaseOrbitScene: React.FC<DatabaseOrbitSceneProps> = ({
  className = "",
  compact = false,
}) => {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) {
      return undefined;
    }

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.set(0, compact ? 0.3 : 0.7, compact ? 7.2 : 8.5);

    const renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const root = new THREE.Group();
    scene.add(root);

    const primary = new THREE.Color("#4f46e5");
    const violet = new THREE.Color("#8b5cf6");
    const cyan = new THREE.Color("#c7d2fe");

    const ambient = new THREE.AmbientLight(0xffffff, 1.2);
    scene.add(ambient);

    const keyLight = new THREE.PointLight(0x8b5cf6, 16, 18);
    keyLight.position.set(-4, 5, 7);
    scene.add(keyLight);

    const rimLight = new THREE.PointLight(0x4f46e5, 10, 18);
    rimLight.position.set(5, -1, 4);
    scene.add(rimLight);

    const diskMaterial = new THREE.MeshPhysicalMaterial({
      color: primary,
      metalness: 0.34,
      roughness: 0.28,
      transmission: 0.1,
      thickness: 0.8,
      transparent: true,
      opacity: 0.92,
      emissive: primary,
      emissiveIntensity: 0.14,
    });

    const capMaterial = new THREE.MeshPhysicalMaterial({
      color: violet,
      metalness: 0.2,
      roughness: 0.22,
      transparent: true,
      opacity: 0.88,
      emissive: violet,
      emissiveIntensity: 0.18,
    });

    const ringMaterial = new THREE.MeshBasicMaterial({
      color: cyan,
      transparent: true,
      opacity: 0.46,
    });

    const cylinderGeometry = new THREE.CylinderGeometry(1.45, 1.45, 0.42, 72, 1);
    const capGeometry = new THREE.TorusGeometry(1.45, 0.035, 12, 96);
    const disposableGeometries: THREE.BufferGeometry[] = [
      cylinderGeometry,
      capGeometry,
    ];

    [-0.82, 0, 0.82].forEach((y, index) => {
      const disk = new THREE.Mesh(cylinderGeometry, diskMaterial);
      disk.position.y = y;
      disk.scale.x = 1 + index * 0.035;
      disk.scale.z = 1 + index * 0.035;
      root.add(disk);

      const cap = new THREE.Mesh(capGeometry, capMaterial);
      cap.rotation.x = Math.PI / 2;
      cap.position.y = y + 0.23;
      cap.scale.x = disk.scale.x;
      cap.scale.y = disk.scale.z;
      root.add(cap);
    });

    const orbitGroup = new THREE.Group();
    root.add(orbitGroup);

    [2.45, 3.15, 3.85].forEach((radius, index) => {
      const geometry = new THREE.TorusGeometry(radius, 0.008, 8, 160);
      disposableGeometries.push(geometry);
      const ring = new THREE.Mesh(
        geometry,
        ringMaterial,
      );
      ring.rotation.x = Math.PI / 2 + index * 0.42;
      ring.rotation.y = index * 0.36;
      orbitGroup.add(ring);
    });

    const nodeGeometry = new THREE.SphereGeometry(0.085, 24, 16);
    disposableGeometries.push(nodeGeometry);
    const nodeMaterial = new THREE.MeshBasicMaterial({ color: 0xc7d2fe });
    const nodes: THREE.Mesh[] = [];
    for (let i = 0; i < 18; i += 1) {
      const node = new THREE.Mesh(nodeGeometry, nodeMaterial);
      const angle = (i / 18) * Math.PI * 2;
      const radius = 2.45 + (i % 3) * 0.58;
      node.position.set(Math.cos(angle) * radius, Math.sin(i) * 0.55, Math.sin(angle) * radius);
      nodes.push(node);
      orbitGroup.add(node);
    }

    const lineMaterial = new THREE.LineBasicMaterial({
      color: 0x818cf8,
      transparent: true,
      opacity: 0.34,
    });
    for (let i = 0; i < nodes.length; i += 1) {
      const next = nodes[(i + 3) % nodes.length];
      const geometry = new THREE.BufferGeometry().setFromPoints([
        nodes[i].position,
        next.position,
      ]);
      disposableGeometries.push(geometry);
      orbitGroup.add(new THREE.Line(geometry, lineMaterial));
    }

    root.rotation.x = -0.18;
    root.rotation.y = 0.38;

    const resize = () => {
      const width = Math.max(mount.clientWidth, 1);
      const height = Math.max(mount.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(mount);
    resize();

    let frameId = 0;
    const animate = () => {
      frameId = window.requestAnimationFrame(animate);
      const time = performance.now() * 0.001;
      root.rotation.y = 0.38 + Math.sin(time * 0.35) * 0.12;
      root.rotation.x = -0.18 + Math.cos(time * 0.28) * 0.05;
      orbitGroup.rotation.y = time * 0.32;
      orbitGroup.rotation.x = Math.sin(time * 0.24) * 0.08;
      nodes.forEach((node, index) => {
        const scale = 1 + Math.sin(time * 1.8 + index) * 0.22;
        node.scale.setScalar(scale);
      });
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      if (renderer.domElement.parentElement === mount) {
        mount.removeChild(renderer.domElement);
      }
      disposableGeometries.forEach((geometry) => geometry.dispose());
      diskMaterial.dispose();
      capMaterial.dispose();
      ringMaterial.dispose();
      nodeMaterial.dispose();
      lineMaterial.dispose();
      renderer.dispose();
    };
  }, [compact]);

  return (
    <div
      ref={mountRef}
      className={`database-orbit-scene ${className}`.trim()}
      aria-hidden="true"
    />
  );
};

export default DatabaseOrbitScene;
