export const LoaderIcon = ({ size = 16 }: { size?: number }) => (
  <svg height={size} viewBox="0 0 16 16" width={size}>
    <g fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M8 0V4" />
      <path d="M8 16V12" opacity="0.5" />
      <path d="M3.3 1.53 5.65 4.76" opacity="0.9" />
      <path d="m12.7 1.53-2.35 3.23" opacity="0.1" />
      <path d="m12.7 14.47-2.35-3.23" opacity="0.4" />
      <path d="m3.3 14.47 2.35-3.23" opacity="0.6" />
      <path d="m15.61 5.53-3.81 1.23" opacity="0.2" />
      <path d="m.39 10.47 3.81-1.23" opacity="0.7" />
      <path d="m15.61 10.47-3.81-1.23" opacity="0.3" />
      <path d="m.39 5.53 3.81 1.23" opacity="0.8" />
    </g>
  </svg>
);

export const CopyIcon = ({ size = 16 }: { size?: number }) => (
  <svg height={size} viewBox="0 0 16 16" width={size}>
    <path
      clipRule="evenodd"
      d="M2.75.5A1.75 1.75 0 0 0 1 2.25v7.5c0 .97.78 1.75 1.75 1.75H4.5V10H2.75a.25.25 0 0 1-.25-.25v-7.5c0-.14.11-.25.25-.25h5.5c.14 0 .25.11.25.25V3H10v-.75A1.75 1.75 0 0 0 8.25.5h-5.5ZM7.75 4.5A1.75 1.75 0 0 0 6 6.25v7.5c0 .97.78 1.75 1.75 1.75h5.5A1.75 1.75 0 0 0 15 13.75v-7.5a1.75 1.75 0 0 0-1.75-1.75h-5.5Zm-.25 1.75c0-.14.11-.25.25-.25h5.5c.14 0 .25.11.25.25v7.5c0 .14-.11.25-.25.25h-5.5a.25.25 0 0 1-.25-.25v-7.5Z"
      fill="currentColor"
      fillRule="evenodd"
    />
  </svg>
);
