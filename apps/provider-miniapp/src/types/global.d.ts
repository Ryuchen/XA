declare module '*.module.scss' {
  const classes: Record<string, string>;
  export default classes;
}

declare module '*.scss';

declare module '*.wav' {
  const url: string;
  export default url;
}
