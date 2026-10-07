<script setup lang="ts">
/**
 * Ellipsis button that opens a small action menu in a teleported popover
 * (DESIGN.md 8.10 popover motion). Closes on selection, Escape or outside click.
 */
import { onBeforeUnmount, ref, watch } from 'vue';
import { useAnchoredPopover, type PopoverPlacement } from '../../composables/useAnchoredPopover';
import IconButton from './IconButton.vue';
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

export interface MenuItem {
  label: string;
  icon?: IconName;
  tone?: 'neutral' | 'danger';
  action: () => void;
}

const props = withDefaults(defineProps<{
  items: MenuItem[];
  label: string;
  placement?: PopoverPlacement;
}>(), { placement: 'bottom-end' });

const anchor = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const popover = useAnchoredPopover(anchor, props.placement);

const toggle = () => (popover.isOpen.value ? popover.close() : popover.open());

const select = (item: MenuItem) => {
  popover.close();
  item.action();
};

const onPointerDown = (e: PointerEvent) => {
  const t = e.target as Node;
  if (!panel.value?.contains(t) && !anchor.value?.contains(t)) popover.close();
};
const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') popover.close(); };

watch(popover.isOpen, open => {
  const method = open ? 'addEventListener' : 'removeEventListener';
  window[method]('pointerdown', onPointerDown as EventListener, true);
  window[method]('keydown', onKey as EventListener);
});
onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', onPointerDown as EventListener, true);
  window.removeEventListener('keydown', onKey as EventListener);
});
</script>

<template>
  <div ref="anchor">
    <IconButton icon="ellipsis-horizontal" :label="label" round :aria-expanded="popover.isOpen.value" @click="toggle" />
    <Teleport to="body">
      <Transition name="popover">
        <div
          v-if="popover.isOpen.value"
          ref="panel"
          role="menu"
          class="material-popover rounded-card fixed z-popover flex min-w-52 flex-col p-1.5"
          :class="placement === 'bottom-end' ? 'origin-top-right' : 'origin-bottom-left'"
          :style="popover.style.value"
        >
          <button
            v-for="item in items"
            :key="item.label"
            type="button"
            role="menuitem"
            class="item type-body flex items-center gap-2.5 rounded-row px-2.5 text-left"
            :class="item.tone === 'danger' ? 'text-danger-fg' : 'text-fg1'"
            @click="select(item)"
          >
            <AppIcon v-if="item.icon" :name="item.icon" />
            {{ item.label }}
          </button>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.item {
  height: var(--control-md);
  transition: background-color var(--dur-fast) var(--ease-out);
}
.item:hover { background: var(--mat-inset-hover); }
</style>
