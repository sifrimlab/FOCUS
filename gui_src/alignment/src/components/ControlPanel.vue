<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { useMainStore } from '../store/main';
import { getReferenceColor, getTargetColor } from '../utils/colors';

const store = useMainStore();
const isDark = ref(false);

onMounted(() => {
  isDark.value = document.documentElement.classList.contains('dark') || window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (isDark.value) document.documentElement.classList.add('dark');
});

const toggleTheme = () => {
  isDark.value = !isDark.value;
  if (isDark.value) {
    document.documentElement.classList.add('dark');
  } else {
    document.documentElement.classList.remove('dark');
  }
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
  // We need to update based on the exact value, not the rounded one
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
    <!-- Sample Header -->
    <div class="flex justify-between items-start">
      <div class="flex-1">
        <h2 class="text-lg font-bold">Sample {{ store.sampleInfo?.sample_id }}</h2>
        <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700 mt-2">
          <div class="bg-blue-600 h-2.5 rounded-full" :style="{ width: progress + '%' }"></div>
        </div>
        <p class="text-sm text-gray-500 mt-1">{{ store.sampleInfo?.sample_index }} / {{ store.sampleInfo?.total_samples_count }}</p>
      </div>
      <button @click="toggleTheme" class="ml-2 p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700">
        {{ isDark ? '☀️' : '🌙' }}
      </button>
    </div>

    <!-- Reference Controls -->
    <div class="border-t pt-4 border-gray-200 dark:border-gray-700">
      <h3 class="font-semibold mb-2">Reference ({{ store.referenceMeta?.modality_type }})</h3>
      <div class="text-sm text-gray-600 dark:text-gray-400 mb-2">
        <div v-if="store.referenceMeta?.modality_type === 'IMAGE'">
          Shape: {{ store.referenceMeta?.image_shape?.join(' x ') }}
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
          <input type="number" step="0.1" v-model.number="store.globalZoom" class="flex-1 h-8 border rounded px-2 text-center dark:bg-gray-800 dark:border-gray-600">
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
          Shape: {{ store.targetMeta?.image_shape?.join(' x ') }}
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
        <label class="text-sm block mb-1">Opacity: {{ (store.targetOpacity * 100).toFixed(0) }}%</label>
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
            <input type="number" step="0.1" v-model.number="currentScale" class="flex-1 h-8 border rounded px-2 text-center dark:bg-gray-800 dark:border-gray-600">
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
            <input type="number" step="1" v-model.number="currentRotation" class="flex-1 h-8 border rounded px-2 text-center dark:bg-gray-800 dark:border-gray-600">
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
