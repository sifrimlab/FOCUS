<script setup lang="ts">
/**
 * Fixed window background (DESIGN.md 5): three soft color fields over the
 * base color. Hues follow the pipeline state and cross-fade through
 * registered custom properties. Fields drift slowly (transform only).
 */
import type { AmbientState } from '../../composables/useAmbientState';

defineProps<{ state: AmbientState }>();
</script>

<template>
  <div class="backdrop" :data-ambient="state" aria-hidden="true">
    <span class="field field--1" />
    <span class="field field--2" />
    <span class="field field--3" />
  </div>
</template>

<style scoped>
@property --field-1 { syntax: "<color>"; inherits: true; initial-value: transparent; }
@property --field-2 { syntax: "<color>"; inherits: true; initial-value: transparent; }
@property --field-3 { syntax: "<color>"; inherits: true; initial-value: transparent; }

.backdrop {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  background: var(--backdrop-base);
  --amp: 1;
  transition:
    --field-1 var(--dur-ambient) var(--ease-in-out),
    --field-2 var(--dur-ambient) var(--ease-in-out),
    --field-3 var(--dur-ambient) var(--ease-in-out);
}

.backdrop[data-ambient="idle"] { --field-1: var(--ambient-idle-1); --field-2: var(--ambient-idle-2); --field-3: var(--ambient-idle-3); --amp: 0.5; }
.backdrop[data-ambient="run"]  { --field-1: var(--ambient-run-1);  --field-2: var(--ambient-run-2);  --field-3: var(--ambient-run-3); }
.backdrop[data-ambient="wait"] { --field-1: var(--ambient-wait-1); --field-2: var(--ambient-wait-2); --field-3: var(--ambient-wait-3); }
.backdrop[data-ambient="done"] { --field-1: var(--ambient-done-1); --field-2: var(--ambient-done-2); --field-3: var(--ambient-done-3); --amp: 0; }
.backdrop[data-ambient="err"]  { --field-1: var(--ambient-err-1);  --field-2: var(--ambient-err-2);  --field-3: var(--ambient-err-3);  --amp: 0; }

.field {
  position: absolute;
  border-radius: 50%;
  opacity: var(--ambient-opacity);
  will-change: transform;
  animation: drift var(--ambient-drift) var(--ease-in-out) infinite alternate;
}
.field--1 {
  width: 70vw; height: 70vw; top: -30vw; left: -20vw;
  background: radial-gradient(closest-side, var(--field-1), transparent);
}
.field--2 {
  width: 60vw; height: 60vw; top: 10vh; right: -25vw;
  background: radial-gradient(closest-side, var(--field-2), transparent);
  animation-delay: calc(var(--ambient-drift) / -3);
}
.field--3 {
  width: 55vw; height: 55vw; bottom: -30vw; left: 20vw;
  background: radial-gradient(closest-side, var(--field-3), transparent);
  animation-delay: calc(var(--ambient-drift) / -1.5);
}

@keyframes drift {
  from { transform: translate(0, 0) scale(0.95); }
  to   { transform: translate(calc(var(--amp) * 6vw), calc(var(--amp) * 4vw)) scale(calc(1 + var(--amp) * 0.05)); }
}

@media (prefers-reduced-motion: reduce) {
  .field { animation: none; }
}
</style>
