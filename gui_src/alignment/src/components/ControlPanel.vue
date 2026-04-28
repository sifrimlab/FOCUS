<script setup lang="ts">
import { computed, ref } from 'vue';
import { useMainStore } from '../store/main';
import { getReferenceColor, getTargetColor } from '../utils/colors';
import logoMark from '../assets/logo-mark.svg';
import logoMarkDark from '../assets/logo-mark-dark.svg';

const store = useMainStore();
const isDark = ref(document.documentElement.classList.contains('dark'));

const markSrc = computed(() => isDark.value ? logoMarkDark : logoMark);

const toggleTheme = () => {
  isDark.value = !isDark.value;
  document.documentElement.classList.toggle('dark', isDark.value);
  localStorage.setItem('focus-theme-override', '1');
};

const progress = computed(() => {
  if (!store.sampleInfo) return 0;
  return (store.sampleInfo.sample_index / store.sampleInfo.total_samples_count) * 100;
});

const sendCommand = (type: 'zoom' | 'rotate' | 'flip' | 'reset' | 'resetDistort' | 'setScale' | 'setRotation' | 'resetScale' | 'resetRotation', value?: any) => {
  store.pendingCommand = { type, value };
};

const currentScale = computed({
  get: () => {
    const m = store.targetTransform;
    const val = Math.hypot(m[0], m[1]);
    return parseFloat(val.toFixed(4));
  },
  set: (val) => {
    sendCommand('setScale', val);
  }
});

const currentRotation = computed({
  get: () => {
    const m = store.targetTransform;
    let deg = Math.atan2(m[1], m[0]) * 180 / Math.PI;
    if (deg < 0) deg += 360;
    return parseFloat((deg % 360).toFixed(4));
  },
  set: (val) => {
    sendCommand('setRotation', val);
  }
});

const updateScale = (delta: number) => {
  const m = store.targetTransform;
  const exactScale = Math.hypot(m[0], m[1]);
  sendCommand('setScale', exactScale + delta);
};

const updateRotation = (delta: number) => {
  const m = store.targetTransform;
  let deg = Math.atan2(m[1], m[0]) * 180 / Math.PI;
  sendCommand('setRotation', deg + delta);
};

let intervalId: any = null;
const startHold = (fn: () => void) => {
  fn();
  intervalId = setInterval(fn, 100);
};
const stopHold = () => {
  if (intervalId) {
    clearInterval(intervalId);
    intervalId = null;
  }
};

const toggleRefClass = (cls: number) => {
  const idx = store.referenceClassFilter.indexOf(cls);
  if (idx === -1) {
    store.referenceClassFilter = [...store.referenceClassFilter, cls];
  } else {
    store.referenceClassFilter = store.referenceClassFilter.filter(c => c !== cls);
  }
};

const toggleTgtClass = (cls: number) => {
  const idx = store.targetClassFilter.indexOf(cls);
  if (idx === -1) {
    store.targetClassFilter = [...store.targetClassFilter, cls];
  } else {
    store.targetClassFilter = store.targetClassFilter.filter(c => c !== cls);
  }
};
</script>

<template>
  <div class="flex flex-col h-full overflow-y-auto text-sm">

    <!-- ── HEADER ───────────────────────────────── -->
    <div class="px-4 pt-4 pb-3 border-b border-gray-200 dark:border-gray-700 space-y-3 shrink-0">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <img :src="markSrc" alt="" width="20" height="20" class="shrink-0" />
          <span class="font-bold text-slate-900 dark:text-slate-100 tracking-tight">FOCUS</span>
          <span class="text-[10px] font-medium uppercase tracking-widest text-gray-400 dark:text-gray-500">ALIGNMENT</span>
        </div>
        <button @click="toggleTheme" class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400">
          <svg v-if="isDark" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707m12.728 0l-.707-.707M6.343 6.343l-.707-.707M12 7a5 5 0 100 10A5 5 0 0012 7z"/>
          </svg>
          <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          </svg>
        </button>
      </div>
      <div>
        <div class="flex justify-between items-baseline mb-1.5">
          <span class="font-semibold font-mono" style="font-feature-settings: 'zero'">{{ store.sampleInfo?.sample_id }}</span>
          <span class="text-xs text-gray-500 font-mono" style="font-feature-settings: 'zero'">
            {{ store.sampleInfo?.sample_index }}&thinsp;/&thinsp;{{ store.sampleInfo?.total_samples_count }}
          </span>
        </div>
        <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
          <div class="bg-blue-500 h-1.5 rounded-full transition-all duration-300" :style="{ width: progress + '%' }"></div>
        </div>
      </div>
    </div>

    <!-- ── TRANSFORMATION CONTROLLERS ─────────── -->
    <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 space-y-2.5 shrink-0">
      <p class="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">Transform</p>

      <!-- Control Mode -->
      <div class="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
        <button
          @click="store.setControlMode('aligner')"
          :class="[
            'flex items-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-all duration-200 select-none',
            store.controlMode === 'aligner'
              ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 flex-1 justify-center px-2'
              : 'text-gray-400 dark:text-gray-500 px-2.5'
          ]"
        >
          <!-- Crosshair icon -->
          <svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <circle cx="12" cy="12" r="4" />
            <line x1="12" y1="2" x2="12" y2="8" />
            <line x1="12" y1="16" x2="12" y2="22" />
            <line x1="2" y1="12" x2="8" y2="12" />
            <line x1="16" y1="12" x2="22" y2="12" />
          </svg>
          <span v-if="store.controlMode === 'aligner'">Aligner</span>
        </button>
        <button
          @click="store.setControlMode('camera')"
          :class="[
            'flex items-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-all duration-200 select-none',
            store.controlMode === 'camera'
              ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 flex-1 justify-center px-2'
              : 'text-gray-400 dark:text-gray-500 px-2.5'
          ]"
        >
          <!-- Pan arrows icon -->
          <svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3" />
            <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
          </svg>
          <span v-if="store.controlMode === 'camera'">Camera</span>
        </button>
      </div>

      <!-- Flip buttons -->
      <div class="flex gap-2">
        <button
          @click="sendCommand('flip', true)"
          class="btn-secondary flex-1 h-8 flex items-center justify-center gap-1.5 select-none"
          title="Flip Horizontal"
        >
          <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="10,8 4,12 10,16" />
            <polyline points="14,8 20,12 14,16" />
            <line x1="12" y1="5" x2="12" y2="19" stroke-dasharray="2.5 1.5" />
          </svg>
          <span class="text-xs font-medium">H</span>
        </button>
        <button
          @click="sendCommand('flip', false)"
          class="btn-secondary flex-1 h-8 flex items-center justify-center gap-1.5 select-none"
          title="Flip Vertical"
        >
          <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="8,10 12,4 16,10" />
            <polyline points="8,14 12,20 16,14" />
            <line x1="5" y1="12" x2="19" y2="12" stroke-dasharray="2.5 1.5" />
          </svg>
          <span class="text-xs font-medium">V</span>
        </button>
      </div>

      <!-- Scale -->
      <div>
        <div class="flex justify-between items-center mb-1">
          <label class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">Scale</label>
          <button @click="sendCommand('resetScale')" class="text-[10px] text-blue-500 hover:text-blue-600 font-medium">Reset</button>
        </div>
        <div class="flex items-center gap-1.5">
          <button
            @mousedown="startHold(() => updateScale(-0.01))" @mouseup="stopHold" @mouseleave="stopHold"
            class="btn-secondary w-7 h-7 flex items-center justify-center text-base leading-none select-none shrink-0"
          >−</button>
          <input type="number" step="0.01" v-model.number="currentScale"
            class="flex-1 h-7 border border-gray-200 dark:border-gray-600 rounded px-1.5 text-center font-mono text-xs bg-white dark:bg-gray-800"
            style="font-feature-settings: 'zero'" />
          <button
            @mousedown="startHold(() => updateScale(0.01))" @mouseup="stopHold" @mouseleave="stopHold"
            class="btn-secondary w-7 h-7 flex items-center justify-center text-base leading-none select-none shrink-0"
          >+</button>
        </div>
      </div>

      <!-- Rotation -->
      <div>
        <div class="flex justify-between items-center mb-1">
          <label class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">Rotation °</label>
          <button @click="sendCommand('resetRotation')" class="text-[10px] text-blue-500 hover:text-blue-600 font-medium">Reset</button>
        </div>
        <div class="flex items-center gap-1.5">
          <button
            @mousedown="startHold(() => updateRotation(-1))" @mouseup="stopHold" @mouseleave="stopHold"
            class="btn-secondary w-7 h-7 flex items-center justify-center text-base leading-none select-none shrink-0"
          >−</button>
          <input type="number" step="1" v-model.number="currentRotation"
            class="flex-1 h-7 border border-gray-200 dark:border-gray-600 rounded px-1.5 text-center font-mono text-xs bg-white dark:bg-gray-800"
            style="font-feature-settings: 'zero'" />
          <button
            @mousedown="startHold(() => updateRotation(1))" @mouseup="stopHold" @mouseleave="stopHold"
            class="btn-secondary w-7 h-7 flex items-center justify-center text-base leading-none select-none shrink-0"
          >+</button>
        </div>
      </div>

      <!-- Reset buttons -->
      <div class="grid grid-cols-2 gap-1.5">
        <button @click="sendCommand('resetDistort')" class="btn-secondary text-xs py-1.5 px-2 select-none">Reset Distortion</button>
        <button @click="sendCommand('reset')" class="btn-secondary text-xs py-1.5 px-2 select-none">Reset Transform</button>
      </div>
    </div>

    <!-- ── TARGET MODALITY ──────────────────── -->
    <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 shrink-0">
      <div class="flex items-center gap-2 mb-2.5">
        <div class="w-2 h-2 rounded-full bg-sky-500 shrink-0"></div>
        <h3 class="font-semibold text-slate-900 dark:text-slate-100 leading-none truncate">
          {{ store.targetMeta?.modality_name || 'Target' }}
        </h3>
        <span class="shrink-0 text-[9px] font-bold uppercase tracking-wider text-sky-600 dark:text-sky-400 bg-sky-50 dark:bg-sky-950 border border-sky-200 dark:border-sky-800 px-1.5 py-0.5 rounded">
          {{ store.targetMeta?.modality_type }}
        </span>
      </div>

      <div class="text-xs text-gray-500 dark:text-gray-400 mb-2.5">
        <div v-if="store.targetMeta?.modality_type === 'IMAGE'">
          <span class="font-mono" style="font-feature-settings: 'zero'">{{ store.targetMeta?.image_shape?.join(' × ') }}</span>&thinsp;px
        </div>
        <div v-else>
          <label class="text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1 block">Spot Size (µm)</label>
          <div class="flex gap-1.5">
            <input type="number" v-model.number="store.targetSpotSize[0]"
              class="w-1/2 border border-gray-200 dark:border-gray-600 rounded px-1.5 h-7 bg-white dark:bg-gray-800 font-mono text-xs" />
            <input type="number" v-model.number="store.targetSpotSize[1]"
              class="w-1/2 border border-gray-200 dark:border-gray-600 rounded px-1.5 h-7 bg-white dark:bg-gray-800 font-mono text-xs" />
          </div>
        </div>
      </div>

      <!-- Opacity -->
      <div class="mb-2.5">
        <div class="flex justify-between items-center mb-1">
          <label class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">Opacity</label>
          <span class="font-mono text-[11px] text-gray-500 dark:text-gray-400" style="font-feature-settings: 'zero'">
            {{ (store.targetOpacity * 100).toFixed(0) }}%
          </span>
        </div>
        <input type="range" min="0" max="1" step="0.05" v-model.number="store.targetOpacity"
          class="w-full h-1.5 cursor-pointer accent-sky-500" />
      </div>

      <div v-if="store.targetMeta?.modality_type === 'SPOT'">
        <h4 class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">Spot Classes</h4>
        <div class="space-y-0.5 max-h-36 overflow-y-auto border border-gray-200 dark:border-gray-600 rounded-lg p-1">
          <div v-for="cls in store.targetSpotClasses" :key="cls"
            class="flex items-center justify-between px-1.5 py-0.5 rounded hover:bg-gray-50 dark:hover:bg-gray-800">
            <div class="w-3 h-3 rounded-sm border border-gray-200 dark:border-gray-600 shrink-0"
              :style="{ backgroundColor: getTargetColor(cls) }"></div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" :checked="store.targetClassFilter.includes(cls)" @change="toggleTgtClass(cls)" class="sr-only peer">
              <div class="w-8 h-4 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-sky-500 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4"></div>
            </label>
          </div>
        </div>
        <div class="flex mt-1 gap-2">
          <button @click="store.targetClassFilter = [...store.targetSpotClasses]" class="text-[10px] text-blue-500 hover:underline">All</button>
          <button @click="store.targetClassFilter = []" class="text-[10px] text-blue-500 hover:underline">None</button>
        </div>

        <h4 class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mt-2.5 mb-1">Foreground</h4>
        <div class="flex bg-gray-100 dark:bg-gray-800 rounded-md p-0.5">
          <button
            @click="store.targetForegroundMode = 'all'"
            :class="['flex-1 py-1 text-xs rounded transition-colors select-none', store.targetForegroundMode === 'all' ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 font-medium' : 'text-gray-400 dark:text-gray-500']"
          >All</button>
          <button
            @click="store.targetForegroundMode = 'foreground'"
            :class="['flex-1 py-1 text-xs rounded transition-colors select-none', store.targetForegroundMode === 'foreground' ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 font-medium' : 'text-gray-400 dark:text-gray-500']"
          >FG</button>
          <button
            @click="store.targetForegroundMode = 'background'"
            :class="['flex-1 py-1 text-xs rounded transition-colors select-none', store.targetForegroundMode === 'background' ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 font-medium' : 'text-gray-400 dark:text-gray-500']"
          >BG</button>
        </div>
      </div>
    </div>

    <!-- ── REFERENCE MODALITY ───────────────── -->
    <div class="px-4 py-3 pb-6 shrink-0">
      <div class="flex items-center gap-2 mb-2.5">
        <div class="w-2 h-2 rounded-full bg-amber-500 shrink-0"></div>
        <h3 class="font-semibold text-slate-900 dark:text-slate-100 leading-none truncate">
          {{ store.referenceMeta?.modality_name || 'Reference' }}
        </h3>
        <span class="shrink-0 text-[9px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 px-1.5 py-0.5 rounded">
          {{ store.referenceMeta?.modality_type }}
        </span>
      </div>

      <div class="text-xs text-gray-500 dark:text-gray-400 mb-2.5">
        <div v-if="store.referenceMeta?.modality_type === 'IMAGE'">
          <span class="font-mono" style="font-feature-settings: 'zero'">{{ store.referenceMeta?.image_shape?.join(' × ') }}</span>&thinsp;px
        </div>
        <div v-else>
          <label class="text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1 block">Spot Size (µm)</label>
          <div class="flex gap-1.5">
            <input type="number" v-model.number="store.referenceSpotSize[0]"
              class="w-1/2 border border-gray-200 dark:border-gray-600 rounded px-1.5 h-7 bg-white dark:bg-gray-800 font-mono text-xs" />
            <input type="number" v-model.number="store.referenceSpotSize[1]"
              class="w-1/2 border border-gray-200 dark:border-gray-600 rounded px-1.5 h-7 bg-white dark:bg-gray-800 font-mono text-xs" />
          </div>
        </div>
      </div>

      <!-- View Zoom -->
      <div class="mb-2.5">
        <div class="flex justify-between items-center mb-1">
          <label class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">View Zoom</label>
          <button @click="store.globalZoom = 1.0" class="text-[10px] text-blue-500 hover:text-blue-600 font-medium">Reset</button>
        </div>
        <div class="flex items-center gap-1.5">
          <button
            @mousedown="startHold(() => store.globalZoom *= 0.98)" @mouseup="stopHold" @mouseleave="stopHold"
            class="btn-secondary w-7 h-7 flex items-center justify-center text-base leading-none select-none shrink-0"
          >−</button>
          <input type="number" step="0.1" v-model.number="store.globalZoom"
            class="flex-1 h-7 border border-gray-200 dark:border-gray-600 rounded px-1.5 text-center font-mono text-xs bg-white dark:bg-gray-800"
            style="font-feature-settings: 'zero'" />
          <button
            @mousedown="startHold(() => store.globalZoom *= 1.02)" @mouseup="stopHold" @mouseleave="stopHold"
            class="btn-secondary w-7 h-7 flex items-center justify-center text-base leading-none select-none shrink-0"
          >+</button>
        </div>
      </div>

      <div v-if="store.referenceMeta?.modality_type === 'SPOT'">
        <h4 class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">Spot Classes</h4>
        <div class="space-y-0.5 max-h-36 overflow-y-auto border border-gray-200 dark:border-gray-600 rounded-lg p-1">
          <div v-for="cls in store.referenceSpotClasses" :key="cls"
            class="flex items-center justify-between px-1.5 py-0.5 rounded hover:bg-gray-50 dark:hover:bg-gray-800">
            <div class="w-3 h-3 rounded-sm border border-gray-200 dark:border-gray-600 shrink-0"
              :style="{ backgroundColor: getReferenceColor(cls) }"></div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" :checked="store.referenceClassFilter.includes(cls)" @change="toggleRefClass(cls)" class="sr-only peer">
              <div class="w-8 h-4 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-amber-500 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4"></div>
            </label>
          </div>
        </div>
        <div class="flex mt-1 gap-2">
          <button @click="store.referenceClassFilter = [...store.referenceSpotClasses]" class="text-[10px] text-blue-500 hover:underline">All</button>
          <button @click="store.referenceClassFilter = []" class="text-[10px] text-blue-500 hover:underline">None</button>
        </div>

        <h4 class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mt-2.5 mb-1">Foreground</h4>
        <div class="flex bg-gray-100 dark:bg-gray-800 rounded-md p-0.5">
          <button
            @click="store.referenceForegroundMode = 'all'"
            :class="['flex-1 py-1 text-xs rounded transition-colors select-none', store.referenceForegroundMode === 'all' ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 font-medium' : 'text-gray-400 dark:text-gray-500']"
          >All</button>
          <button
            @click="store.referenceForegroundMode = 'foreground'"
            :class="['flex-1 py-1 text-xs rounded transition-colors select-none', store.referenceForegroundMode === 'foreground' ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 font-medium' : 'text-gray-400 dark:text-gray-500']"
          >FG</button>
          <button
            @click="store.referenceForegroundMode = 'background'"
            :class="['flex-1 py-1 text-xs rounded transition-colors select-none', store.referenceForegroundMode === 'background' ? 'bg-white dark:bg-gray-700 shadow text-slate-900 dark:text-slate-100 font-medium' : 'text-gray-400 dark:text-gray-500']"
          >BG</button>
        </div>
      </div>

      <button @click="store.confirm" class="w-full btn-primary mt-3 select-none">Confirm Alignment</button>
    </div>

    <!-- Error Toast -->
    <div v-if="store.error" class="fixed bottom-4 right-4 bg-red-500 text-white p-3 rounded-lg shadow-lg z-50 text-sm flex items-center gap-2">
      {{ store.error }}
      <button @click="store.error = null" class="font-bold hover:text-red-200">✕</button>
    </div>
  </div>
</template>
