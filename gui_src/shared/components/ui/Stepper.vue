<script setup lang="ts" generic="Id extends string">
/**
 * Horizontal step indicator for guided flows (DESIGN.md 12.4). Done and
 * available steps are buttons; locked steps are inert. Same circle language
 * as the pipeline stage stepper (DESIGN.md 8.7). `stretch` spreads the
 * steps over the full width, with connectors filling the space between them.
 */
import AppIcon from './AppIcon.vue';

export type StepperState = 'done' | 'current' | 'available' | 'locked';

defineProps<{
  steps: { id: Id; label: string; state: StepperState }[];
  label: string;
  stretch?: boolean;
}>();

const emit = defineEmits<{ select: [id: Id] }>();
</script>

<template>
  <nav :aria-label="label">
    <ol class="flex items-center gap-1" :class="{ 'w-full': stretch }">
      <template v-for="(step, i) in steps" :key="step.id">
        <li>
          <button
            type="button"
            class="step"
            :class="`step--${step.state}`"
            :disabled="step.state === 'locked'"
            :aria-current="step.state === 'current' ? 'step' : undefined"
            @click="step.state !== 'current' && emit('select', step.id)"
          >
            <span class="step__dot type-caption nums">
              <AppIcon v-if="step.state === 'done'" name="check" :size="12" />
              <span v-else>{{ i + 1 }}</span>
            </span>
            <span class="type-body-strong">{{ step.label }}</span>
          </button>
        </li>
        <li v-if="i < steps.length - 1" class="connector" :class="{ 'connector--done': step.state === 'done', 'connector--stretch': stretch }" aria-hidden="true" />
      </template>
    </ol>
  </nav>
</template>

<style scoped>
.step {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: var(--control-md);
  padding: 0 12px 0 4px;
  border-radius: var(--radius-full);
  color: var(--fg3);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.step:not(:disabled):not(.step--current):hover { background: var(--mat-inset-fill); color: var(--fg1); }
.step--current { color: var(--fg1); background: var(--primary-soft); cursor: default; }
.step--done { color: var(--fg2); }
.step--locked { opacity: 0.5; cursor: not-allowed; }

.step__dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-full);
  background: var(--mat-inset-fill);
  color: var(--fg3);
  transition: background-color var(--dur-base) var(--ease-out), color var(--dur-base) var(--ease-out);
}
.step--current .step__dot { background: var(--primary-fill); color: var(--fg-inverse); }
.step--done .step__dot { background: var(--success); color: var(--fg-inverse); }

.connector {
  width: 20px;
  height: 2px;
  border-radius: var(--radius-full);
  background: var(--separator-strong);
  transition: background-color var(--dur-slow) var(--ease-out);
}
.connector--done { background: var(--success); }
.connector--stretch { flex: 1; min-width: 20px; }
</style>
