<script setup lang="ts">
/** App shell: backdrop, chrome, and either the workspace or a full-screen state. */
import { onMounted } from 'vue';
import AmbientBackdrop from '@focus/ui/components/shell/AmbientBackdrop.vue';
import ChromeCluster from '@focus/ui/components/shell/ChromeCluster.vue';
import StatusScreen from '@focus/ui/components/ui/StatusScreen.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import { useMainStore } from './store/main';
import { useAppScreen } from './composables/useAppScreen';
import AlignmentWorkspace from './workspace/AlignmentWorkspace.vue';

const store = useMainStore();
const screen = useAppScreen();

onMounted(() => {
  store.fetchNextSample();
});
</script>

<template>
  <AmbientBackdrop :state="screen.ambient" />
  <ChromeCluster collapsible />

  <!-- No transition around the workspace: its canvases must unmount the moment
       a sample starts loading, before the next sample's data arrives, or their
       watchers would act on it and change the next sample's starting transform. -->
  <AlignmentWorkspace v-if="screen.kind === 'workspace'" />
  <StatusScreen v-else :key="screen.kind" v-bind="screen.card">
    <template v-if="screen.action" #footer>
      <BaseButton variant="primary" size="lg" :icon="screen.action.icon" class="mt-2" @click="screen.action.run">
        {{ screen.action.label }}
      </BaseButton>
    </template>
  </StatusScreen>
</template>
