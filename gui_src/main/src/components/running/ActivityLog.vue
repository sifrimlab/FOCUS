<script setup lang="ts">
/**
 * Latest pipeline messages, newest first, with the server time they arrived.
 * The card fills the height its parent gives it; only the list scrolls.
 */
import { computed } from 'vue';
import { injectRunProgress } from '../../composables/useRunProgress';
import { formatClock } from '../../utils/duration';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';

const run = injectRunProgress();
const entries = computed(() => [...run.status.value.messages].reverse());
</script>

<template>
  <GlassCard class="flex min-h-0 flex-col gap-3" role="log" aria-label="Activity" aria-live="polite">
    <CardHeader title="Activity" level="section" />
    <p v-if="entries.length === 0" class="type-callout text-fg3">Waiting for the first update…</p>
    <ol v-else class="log flex flex-col overflow-y-auto">
      <TransitionGroup name="list">
        <li
          v-for="(e, i) in entries"
          :key="e.t + e.text"
          class="flex gap-3 py-1.5"
          :class="{ 'border-t border-separator': i > 0 }"
        >
          <time class="type-mono-small text-fg3 shrink-0 pt-px">{{ formatClock(e.t) }}</time>
          <span class="type-callout break-words" :class="i === 0 ? 'text-fg1' : 'text-fg2'">{{ e.text }}</span>
        </li>
      </TransitionGroup>
    </ol>
  </GlassCard>
</template>

<style scoped>
.log {
  flex: 1;
  min-height: 0;
  /* Bounded when stacked; a parent that sizes the card sets --log-max-height: none. */
  max-height: var(--log-max-height, 50vh);
  /* Soft fade where older lines scroll out of view. */
  -webkit-mask-image: linear-gradient(to bottom, black calc(100% - 28px), transparent);
  mask-image: linear-gradient(to bottom, black calc(100% - 28px), transparent);
  padding-bottom: 20px;
}
</style>
