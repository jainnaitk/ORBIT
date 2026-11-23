import { useState } from 'react';

interface OrbitLogoProps {
  size?: number;
  className?: string;
}

export const OrbitLogo = ({ size = 30, className = '' }: OrbitLogoProps) => {
  const [imgFailed, setImgFailed] = useState(false);

  if (!imgFailed) {
    return (
      <div
        style={{
          width: size,
          height: size,
          boxShadow: '0 0 16px rgba(var(--dynamic-accent-rgb), 0.35)',
        }}
        className={`relative rounded-full overflow-hidden flex-none ring-1 ring-white/20 bg-black ${className}`}
      >
        <img
          src="/orbit-logo.png"
          alt="ORBIT"
          draggable={false}
          onError={() => setImgFailed(true)}
          className="w-full h-full object-cover scale-125"
        />
      </div>
    );
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`flex-none drop-shadow-[0_0_10px_rgba(45,212,191,0.4)] ${className}`}
    >
      <defs>
        <radialGradient id="bhCoreGlow" cx="50%" cy="50%" r="50%">
          <stop offset="50%" stopColor="#000000" />
          <stop offset="72%" stopColor="#2DD4BF" stopOpacity="0.9" />
          <stop offset="86%" stopColor="#6366F1" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#000000" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="29" fill="url(#bhCoreGlow)" />
      <ellipse
        cx="32"
        cy="32"
        rx="28"
        ry="12"
        transform="rotate(20 32 32)"
        stroke="#5EEAD4"
        strokeWidth="2"
        strokeOpacity="0.85"
      />
      <circle cx="32" cy="32" r="14.5" fill="#010205" stroke="#5EEAD4" strokeOpacity="0.6" strokeWidth="1.2" />
    </svg>
  );
};
