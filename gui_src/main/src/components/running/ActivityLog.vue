<script setup lang="ts">
/**
 * Activity lines from the backend reporter, newest first. Each progress line
 * keeps its detail and warning lines under it. The card fills the height its
 * parent gives it; only the list scrolls.
 */
import { computed } from 'vue';
import type { ActivityEntry } from '../../api/types';
import { injectRunProgress } from '../../composables/useRunProgress';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import AppIcon from '../ui/AppIcon.vue';
import ActivityLine from './ActivityLine.vue';

interface Group {
  key: number;
  head: ActivityEntry | null;
  notes: ActivityEntry[];
}

const run = injectRunProgress();

/** Attach detail / warning lines to the line before them, then list the newest group first. */
const groups = computed(() => {
  const out: Group[] = [];
  for (const e of run.status.value.activity) {
    const isNote = e.kind === 'detail' || e.kind === 'warning';
    const last = out[out.length - 1];
    if (isNote && last) last.notes.push(e);
    else out.push({ key: e.id, head: isNote ? null : e, notes: isNote ? [e] : [] });
  }
  return out.reverse();
});
</script>

<template>
  <GlassCard class="flex min-h-0 flex-col gap-3" role="log" aria-label="Activity" aria-live="polite">
    <CardHeader title="Activity" level="section" />
    <p v-if="groups.length === 0" class="type-callout text-fg3">Waiting for the first update…</p>
    <ol v-else class="log flex flex-col overflow-y-auto">
      <TransitionGroup name="list">
        <li
          v-for="(g, i) in groups"
          :key="g.key"
          class="group py-1.5"
          :class="{ 'border-t border-separator': i > 0 }"
        >
          <ActivityLine v-if="g.head" :entry="g.head" :latest="i === 0" />
          <p
            v-for="n in g.notes"
            :key="n.id"
            class="note type-footnote flex items-start gap-1.5"
            :class="n.kind === 'warning' ? 'text-warning-fg' : 'text-fg3'"
          >
            <AppIcon v-if="n.kind === 'warning'" name="exclamation-triangle" :size="12" class="mt-0.5 shrink-0" />
            <span class="break-words">{{ n.text }}</span>
          </p>
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
/* Time column, then the line; notes sit under the line, in its column. */
.group {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  column-gap: 12px;
  row-gap: 2px;
}
.note { grid-column: 2; }
</style>
