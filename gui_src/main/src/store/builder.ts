/**
 * Navigation state of the guided configuration builder.
 *
 * UI state only: it is never sent to the backend. The configuration itself
 * lives in the main store; this store decides which step is shown, which
 * steps are unlocked, which modality is being edited, and whether the user
 * came from Review to make a targeted change ("edit mode").
 */
import { defineStore } from 'pinia';
import { useMainStore } from './main';

export type BuilderStep = 'samples' | 'modalities' | 'settings' | 'review';
export type BuilderEntry = 'samples' | 'review';

export const BUILDER_STEPS: { id: BuilderStep; label: string }[] = [
  { id: 'samples', label: 'Samples' },
  { id: 'modalities', label: 'Modalities' },
  { id: 'settings', label: 'Settings' },
  { id: 'review', label: 'Review' },
];

const LAST = BUILDER_STEPS.length - 1;
const indexOf = (step: BuilderStep) => BUILDER_STEPS.findIndex(s => s.id === step);

export type StepState = 'done' | 'current' | 'available' | 'locked';

export const useBuilderStore = defineStore('builder', {
  state: () => ({
    step: 'samples' as BuilderStep,
    /** Highest step index the user may jump to. */
    reached: 0,
    activeModality: 0,
    /** Names of modalities whose settings page has been shown. */
    visited: [] as string[],
    /** True when the user left Review to change one thing and should be offered a way straight back. */
    editingFromReview: false,
  }),

  getters: {
    index: state => indexOf(state.step),

    allVisited(state): boolean {
      const main = useMainStore();
      return main.config.modalities.every(m => state.visited.includes(m.name));
    },

    /** Why the current step cannot be left forward, or null when it can. */
    blockReason(): string | null {
      const main = useMainStore();
      const cfg = main.config;
      if (this.step === 'samples') {
        if (main.samples.length === 0) return 'Add at least one sample to continue.';
        if (main.samples.every(id => cfg.ignore_samples.includes(id))) return 'Include at least one sample to continue.';
      }
      if (this.step === 'modalities' || this.step === 'settings' || this.step === 'review') {
        if (cfg.modalities.length === 0) return 'Add at least one modality to continue.';
        if (!cfg.modalities.some(m => m.name === cfg.reference_modality)) return 'Choose the reference modality to continue.';
      }
      return null;
    },

    stepStates(): StepState[] {
      return BUILDER_STEPS.map((s, i) => {
        if (s.id === this.step) return 'current';
        if (i > this.reached) return 'locked';
        if (s.id === 'settings' && !this.allVisited) return 'available';
        return i < this.reached || this.reached === LAST ? 'done' : 'available';
      });
    },
  },

  actions: {
    /** Enter the builder: on Samples for a new config, on Review for a complete one. */
    start(entry: BuilderEntry) {
      const main = useMainStore();
      this.editingFromReview = false;
      this.activeModality = 0;
      if (entry === 'review') {
        this.step = 'review';
        this.reached = LAST;
        this.visited = main.config.modalities.map(m => m.name);
      } else {
        this.step = 'samples';
        this.reached = 0;
        this.visited = [];
      }
    },

    /** Jump to an unlocked step. From Review, the jump is a targeted edit. */
    goTo(step: BuilderStep) {
      const target = indexOf(step);
      if (target > this.reached || step === this.step) return;
      if (this.step === 'review' && step !== 'review') this.editingFromReview = true;
      if (step === 'review') this.editingFromReview = false;
      this.step = step;
    },

    next() {
      if (this.blockReason) return;
      const count = useMainStore().config.modalities.length;
      if (this.step === 'settings' && this.activeModality < count - 1 && !this.editingFromReview) {
        this.activeModality += 1;
        return;
      }
      const target = Math.min(this.index + 1, LAST);
      this.reached = Math.max(this.reached, target);
      this.step = BUILDER_STEPS[target]!.id;
      if (this.step === 'settings') this.activeModality = 0;
      if (this.step === 'review') this.editingFromReview = false;
    },

    back() {
      if (this.step === 'settings' && this.activeModality > 0) {
        this.activeModality -= 1;
        return;
      }
      if (this.index === 0) return;
      this.step = BUILDER_STEPS[this.index - 1]!.id;
      if (this.step === 'settings') {
        this.activeModality = Math.max(0, useMainStore().config.modalities.length - 1);
      }
    },

    /** Show one modality's settings; from Review this is a targeted edit. */
    openModality(index: number, fromReview = false) {
      this.activeModality = index;
      this.step = 'settings';
      this.reached = Math.max(this.reached, indexOf('settings'));
      if (fromReview) this.editingFromReview = true;
    },

    returnToReview() {
      if (this.blockReason) return;
      this.editingFromReview = false;
      this.reached = LAST;
      this.step = 'review';
    },

    markVisited(name: string) {
      if (name && !this.visited.includes(name)) this.visited.push(name);
    },

    forgetVisited(name: string) {
      this.visited = this.visited.filter(n => n !== name);
    },

    renameVisited(from: string, to: string) {
      this.visited = this.visited.map(n => (n === from ? to : n));
    },

    /** Keep the active modality index valid after removals. */
    clampActiveModality() {
      const count = useMainStore().config.modalities.length;
      this.activeModality = Math.min(this.activeModality, Math.max(0, count - 1));
    },
  },
});
