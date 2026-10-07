/** Ambient backdrop states (DESIGN.md 5.1). Each app derives one from its own state. */
export type AmbientState =
  | 'idle' | 'run' | 'wait' | 'done' | 'err'
  | 'pre' | 'align' | 'annot' | 'reg' | 'comp';
