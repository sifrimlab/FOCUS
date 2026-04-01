<script setup lang="ts">
defineProps<{ files: string[] }>();
</script>

<template>
  <div v-if="files.length > 0" class="bg-white dark:bg-gray-800 rounded-lg shadow p-5 border border-gray-200 dark:border-gray-700">
    <h3 class="font-semibold mb-3">Generated Files</h3>
    <div class="max-h-80 overflow-y-auto">
      <table class="w-full text-sm">
        <tbody>
          <tr v-for="(f, i) in files" :key="i" class="border-b border-gray-100 dark:border-gray-700">
            <td class="py-2 font-mono text-xs break-all">{{ f }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="text-xs text-gray-400 mt-2">{{ files.length }} file(s) generated</p>
  </div>
</template>
