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

const sendCommand = (type: 'zoom' | 'rotate' | 'flip' | 'reset' | 'setScale' | 'setRotation' | 'resetScale' | 'resetRotation', value?: any) => {
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

// Long press logic
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
  <div class="p-4 space-y-6">

    <!-- Brand row -->
    <div class="flex items-center justify-between pb-3 border-b border-gray-200 dark:border-gray-700 -mt-1">
      <div class="flex items-center gap-2">
        <img :src="markSrc" alt="" width="22" height="22" class="shrink-0" />
        <span
          class="text-sm font-bold text-slate-900 dark:text-slate-100"
          style="letter-spacing: -0.02em;"
        >FOCUS</span>
      </div>
      <span class="text-[11px] font-medium uppercase tracking-widest text-gray-500 dark:text-gray-400">
        ALIGNMENT
      </span>
    </div>

    <!-- Sample Header -->
    <div class="flex justify-between items-start">
      <div class="flex-1">
        <h2 class="text-lg font-bold">Sample <span class="font-mono" style="font-feature-settings: 'zero'">{{ store.sampleInfo?.sample_id }}</span></h2>
        <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700 mt-2">
          <div class="bg-blue-600 h-2.5 rounded-full" :style="{ width: progress + '%' }"></div>
        </div>
        <p class="text-sm text-gray-500 mt-1 font-mono" style="font-feature-settings: 'zero'">{{ store.sampleInfo?.sample_index }} / {{ store.sampleInfo?.total_samples_count }}</p>
      </div>
      <button @click="toggleTheme" class="ml-2 p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700">
        <svg v-if="isDark" class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707m12.728 0l-.707-.707M6.343 6.343l-.707-.707M12 7a5 5 0 100 10A5 5 0 0012 7z"/>
        </svg>
        <svg v-else class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      </button>
    </div>

    <!-- Reference Controls -->
    <div class="border-t pt-4 border-gray-200 dark:border-gray-700">
      <h3 class="font-semibold mb-2">Reference ({{ store.referenceMeta?.modality_type }})</h3>
      <div class="text-sm text-gray-600 dark:text-gray-400 mb-2">
        <div v-if="store.referenceMeta?.modality_type === 'IMAGE'">
          Shape: <span class="font-mono" style="font-feature-settings: 'zero'">{{ store.referenceMeta?.image_shape?.join(' x ') }}</span>
        </div>
        <div v-else>
          <div class="flex items-center justify-between mb-1">
             <span>Spot Size (µm)</span>
          </div>
          <div class="flex space-x-2">
             <input type="number" v-model.number="store.referenceSpotSize[0]" class="w-1/2 border rounded px-1 dark:bg-gray-800 dark:border-gray-600">
             <input type="number" v-model.number="store.referenceSpotSize[1]" class="w-1/2 border rounded px-1 dark:bg-gray-800 dark:border-gray-600">
          </div>
        </div>
      </div>

      <div class="flex items-center space-x-2 mb-2">
        <label class="text-sm">Control Mode:</label>
        <div class="flex bg-gray-200 dark:bg-gray-700 rounded p-1 flex-1">
            <button
                @click="store.setControlMode('aligner')"
                :class="['flex-1 py-1 text-xs rounded', store.controlMode === 'aligner' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >Aligner</button>
            <button
                @click="store.setControlMode('camera')"
                :class="['flex-1 py-1 text-xs rounded', store.controlMode === 'camera' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >Camera</button>
        </div>
      </div>

      <div v-if="store.controlMode === 'aligner'" class="flex items-center space-x-2 mb-2">
        <label class="text-sm">Interaction:</label>
        <div class="flex bg-gray-200 dark:bg-gray-700 rounded p-1 flex-1">
            <button
                @click="store.setAlignerInteraction('translate')"
                :class="['flex-1 py-1 text-xs rounded', store.alignerInteraction === 'translate' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >Translate</button>
            <button
                @click="store.setAlignerInteraction('rotate')"
                :class="['flex-1 py-1 text-xs rounded', store.alignerInteraction === 'rotate' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >Rotate</button>
        </div>
      </div>

      <div class="flex justify-between items-center mb-1">
          <label class="text-sm">View Zoom</label>
          <button @click="store.globalZoom = 1.0" class="text-xs text-blue-500 hover:underline">Reset</button>
      </div>
      <div class="flex items-center space-x-2 mb-4">
          <button
              @mousedown="startHold(() => store.globalZoom *= 0.98)"
              @mouseup="stopHold"
              @mouseleave="stopHold"
              class="btn-secondary w-8 h-8 flex items-center justify-center select-none"
          >-</button>
          <input type="number" step="0.1" v-model.number="store.globalZoom" class="flex-1 h-8 border rounded px-2 text-center dark:bg-gray-800 dark:border-gray-600 font-mono" style="font-feature-settings: 'zero'">
          <button
              @mousedown="startHold(() => store.globalZoom *= 1.02)"
              @mouseup="stopHold"
              @mouseleave="stopHold"
              class="btn-secondary w-8 h-8 flex items-center justify-center select-none"
          >+</button>
      </div>

      <div v-if="store.referenceMeta?.modality_type === 'SPOT'">
        <h4 class="text-sm font-semibold mt-2 mb-1">Visualized Spot Classes</h4>
        <div class="space-y-1 max-h-40 overflow-y-auto border rounded p-1 dark:border-gray-600">
            <div v-for="cls in store.referenceSpotClasses" :key="cls" class="flex items-center justify-between p-1 hover:bg-gray-100 dark:hover:bg-gray-800">
                <div class="flex items-center space-x-2">
                    <div class="w-4 h-4 rounded border border-gray-300" :style="{ backgroundColor: getReferenceColor(cls) }"></div>
                </div>
                <label class="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" :checked="store.referenceClassFilter.includes(cls)" @change="toggleRefClass(cls)" class="sr-only peer">
                    <div class="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                </label>
            </div>
        </div>
        <div class="flex mt-1">
            <button @click="store.referenceClassFilter = [...store.referenceSpotClasses]" class="text-xs text-blue-500 hover:underline mr-2">Select All</button>
            <button @click="store.referenceClassFilter = []" class="text-xs text-blue-500 hover:underline">Clear</button>
        </div>

        <h4 class="text-sm font-semibold mt-2 mb-1">Foreground Filter</h4>
        <div class="flex bg-gray-200 dark:bg-gray-700 rounded p-1">
            <button
                @click="store.referenceForegroundMode = 'all'"
                :class="['flex-1 py-1 text-xs rounded', store.referenceForegroundMode === 'all' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >All</button>
            <button
                @click="store.referenceForegroundMode = 'foreground'"
                :class="['flex-1 py-1 text-xs rounded', store.referenceForegroundMode === 'foreground' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >FG</button>
            <button
                @click="store.referenceForegroundMode = 'background'"
                :class="['flex-1 py-1 text-xs rounded', store.referenceForegroundMode === 'background' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >BG</button>
        </div>
      </div>
    </div>

    <!-- Target Controls -->
    <div class="border-t pt-4 border-gray-200 dark:border-gray-700">
      <h3 class="font-semibold mb-2">Target ({{ store.targetMeta?.modality_type }})</h3>

      <div class="text-sm text-gray-600 dark:text-gray-400 mb-2">
        <div v-if="store.targetMeta?.modality_type === 'IMAGE'">
          Shape: <span class="font-mono" style="font-feature-settings: 'zero'">{{ store.targetMeta?.image_shape?.join(' x ') }}</span>
        </div>
        <div v-else>
          <div class="flex items-center justify-between mb-1">
             <span>Spot Size (µm)</span>
          </div>
          <div class="flex space-x-2">
             <input type="number" v-model.number="store.targetSpotSize[0]" class="w-1/2 border rounded px-1 dark:bg-gray-800 dark:border-gray-600">
             <input type="number" v-model.number="store.targetSpotSize[1]" class="w-1/2 border rounded px-1 dark:bg-gray-800 dark:border-gray-600">
          </div>
        </div>
      </div>

      <div class="mb-4">
        <label class="text-sm block mb-1">Opacity: <span class="font-mono" style="font-feature-settings: 'zero'">{{ (store.targetOpacity * 100).toFixed(0) }}</span>%</label>
        <input type="range" min="0" max="1" step="0.1" v-model.number="store.targetOpacity" class="w-full">
      </div>

      <div v-if="store.targetMeta?.modality_type === 'SPOT'" class="mb-4">
        <h4 class="text-sm font-semibold mb-1">Visualized Spot Classes</h4>
        <div class="space-y-1 max-h-40 overflow-y-auto border rounded p-1 dark:border-gray-600">
            <div v-for="cls in store.targetSpotClasses" :key="cls" class="flex items-center justify-between p-1 hover:bg-gray-100 dark:hover:bg-gray-800">
                <div class="flex items-center space-x-2">
                    <div class="w-4 h-4 rounded border border-gray-300" :style="{ backgroundColor: getTargetColor(cls) }"></div>
                </div>
                <label class="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" :checked="store.targetClassFilter.includes(cls)" @change="toggleTgtClass(cls)" class="sr-only peer">
                    <div class="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                </label>
            </div>
        </div>
        <div class="flex mt-1">
            <button @click="store.targetClassFilter = [...store.targetSpotClasses]" class="text-xs text-blue-500 hover:underline mr-2">Select All</button>
            <button @click="store.targetClassFilter = []" class="text-xs text-blue-500 hover:underline">Clear</button>
        </div>

        <h4 class="text-sm font-semibold mt-2 mb-1">Foreground Filter</h4>
        <div class="flex bg-gray-200 dark:bg-gray-700 rounded p-1">
            <button
                @click="store.targetForegroundMode = 'all'"
                :class="['flex-1 py-1 text-xs rounded', store.targetForegroundMode === 'all' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >All</button>
            <button
                @click="store.targetForegroundMode = 'foreground'"
                :class="['flex-1 py-1 text-xs rounded', store.targetForegroundMode === 'foreground' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >FG</button>
            <button
                @click="store.targetForegroundMode = 'background'"
                :class="['flex-1 py-1 text-xs rounded', store.targetForegroundMode === 'background' ? 'bg-white dark:bg-gray-600 shadow font-bold' : 'text-gray-500 dark:text-gray-400']"
            >BG</button>
        </div>
      </div>

      <!-- Scale Control -->
      <div class="mb-4">
        <div class="flex justify-between items-center mb-1">
            <label class="text-sm">Scale</label>
            <button @click="sendCommand('resetScale')" class="text-xs text-blue-500 hover:underline">Reset</button>
        </div>
        <div class="flex items-center space-x-2">
            <button
                @mousedown="startHold(() => updateScale(-0.01))"
                @mouseup="stopHold"
                @mouseleave="stopHold"
                class="btn-secondary w-8 h-8 flex items-center justify-center select-none"
            >-</button>
            <input type="number" step="0.1" v-model.number="currentScale" class="flex-1 h-8 border rounded px-2 text-center dark:bg-gray-800 dark:border-gray-600 font-mono" style="font-feature-settings: 'zero'">
            <button
                @mousedown="startHold(() => updateScale(0.01))"
                @mouseup="stopHold"
                @mouseleave="stopHold"
                class="btn-secondary w-8 h-8 flex items-center justify-center select-none"
            >+</button>
        </div>
      </div>

      <!-- Rotation Control -->
      <div class="mb-4">
        <div class="flex justify-between items-center mb-1">
            <label class="text-sm">Rotation (°)</label>
            <button @click="sendCommand('resetRotation')" class="text-xs text-blue-500 hover:underline">Reset</button>
        </div>
        <div class="flex items-center space-x-2">
            <button
                @mousedown="startHold(() => updateRotation(-1))"
                @mouseup="stopHold"
                @mouseleave="stopHold"
                class="btn-secondary w-8 h-8 flex items-center justify-center select-none"
            >-</button>
            <input type="number" step="1" v-model.number="currentRotation" class="flex-1 h-8 border rounded px-2 text-center dark:bg-gray-800 dark:border-gray-600 font-mono" style="font-feature-settings: 'zero'">
            <button
                @mousedown="startHold(() => updateRotation(1))"
                @mouseup="stopHold"
                @mouseleave="stopHold"
                class="btn-secondary w-8 h-8 flex items-center justify-center select-none"
            >+</button>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-2 mb-4">
        <button @click="sendCommand('flip', true)" class="btn-secondary">Flip H</button>
        <button @click="sendCommand('flip', false)" class="btn-secondary">Flip V</button>
      </div>

      <button @click="sendCommand('reset')" class="w-full btn-secondary mb-2">Reset Transform</button>
      <button @click="store.confirm" class="w-full btn-primary">Confirm Alignment</button>
    </div>

    <!-- Error Toast -->
    <div v-if="store.error" class="fixed bottom-4 right-4 bg-red-500 text-white p-4 rounded shadow-lg z-50">
        {{ store.error }}
        <button @click="store.error = null" class="ml-2 font-bold">X</button>
    </div>
  </div>
</template>
