<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId } from 'vue'

type Option = { value: string; label: string }
const props = withDefaults(defineProps<{
  modelValue?: string
  options: Option[]
  placeholder?: string
  ariaLabel: string
  required?: boolean
}>(), { modelValue: '', placeholder: '请选择', required: false })
const emit = defineEmits<{
  'update:modelValue': [value: string]
  change: [value: string]
}>()
const id = useId()
const trigger = ref<HTMLButtonElement>()
const menu = ref<HTMLElement>()
const open = ref(false)
const active = ref(-1)
const menuStyle = ref<Record<string, string>>({})
const selected = computed(() => props.options.find(option => option.value === props.modelValue))

function position() {
  if (!trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const below = window.innerHeight - rect.bottom - 16
  const above = rect.top - 16
  const upwards = below < 160 && above > below
  const height = Math.min(280, upwards ? above : below)
  menuStyle.value = {
    left: `${Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8))}px`,
    width: `${Math.min(rect.width, window.innerWidth - 16)}px`,
    maxHeight: `${Math.max(48, height)}px`,
    ...(upwards ? { bottom: `${window.innerHeight - rect.top + 8}px` } : { top: `${rect.bottom + 8}px` }),
  }
}
function outside(event: Event) {
  const target = event.target as Node
  if (!trigger.value?.contains(target) && !menu.value?.contains(target)) close()
}
function onScroll(event: Event) {
  if (!menu.value?.contains(event.target as Node)) position()
}
function close() {
  open.value = false
  document.removeEventListener('pointerdown', outside, true)
  window.removeEventListener('resize', position)
  window.removeEventListener('scroll', onScroll, true)
}
async function show(last = false) {
  if (open.value) return
  open.value = true
  const index = props.options.findIndex(option => option.value === props.modelValue)
  active.value = index >= 0 ? index : last ? props.options.length - 1 : 0
  position()
  document.addEventListener('pointerdown', outside, true)
  window.addEventListener('resize', position)
  window.addEventListener('scroll', onScroll, true)
  await nextTick()
  reveal()
}
function reveal() {
  menu.value?.querySelector<HTMLElement>(`[data-index="${active.value}"]`)?.scrollIntoView({ block: 'nearest' })
}
function choose(index: number) {
  const option = props.options[index]
  if (!option) return
  emit('update:modelValue', option.value)
  emit('change', option.value)
  close()
  trigger.value?.focus()
}
async function keydown(event: KeyboardEvent) {
  if (event.key === 'Tab') { close(); return }
  if (event.key === 'Escape') { if (open.value) { event.preventDefault(); event.stopPropagation(); close() }; return }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
    event.preventDefault()
    if (!open.value) { await show(event.key === 'ArrowUp' || event.key === 'End'); return }
    if (event.key === 'Enter' || event.key === ' ') { choose(active.value); return }
    if (event.key === 'Home') active.value = 0
    else if (event.key === 'End') active.value = props.options.length - 1
    else active.value = Math.max(0, Math.min(props.options.length - 1, active.value + (event.key === 'ArrowDown' ? 1 : -1)))
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    await show()
    const index = props.options.findIndex(option => option.label.toLowerCase().startsWith(event.key.toLowerCase()))
    if (index >= 0) active.value = index
  }
  await nextTick()
  reveal()
}
function invalid() { trigger.value?.focus(); void show() }
onBeforeUnmount(close)
</script>

<template>
  <div class="app-select">
    <button ref="trigger" type="button" class="select-trigger" :class="{ 'is-open': open, 'is-placeholder': !selected }"
      role="combobox" aria-haspopup="listbox" :aria-label="ariaLabel" :aria-expanded="open" :aria-controls="`${id}-list`"
      :aria-required="required" :aria-activedescendant="open && options[active] ? `${id}-option-${active}` : undefined"
      @click="open ? close() : show()" @keydown="keydown" @blur="close">
      <span class="select-value">{{ selected?.label ?? placeholder }}</span>
      <svg class="select-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 7.5 5 5 5-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <input v-if="required" class="select-validation" :value="modelValue" required tabindex="-1" aria-hidden="true" @invalid.prevent="invalid">
    <Teleport to="body">
      <div v-if="open" :id="`${id}-list`" ref="menu" class="select-menu" role="listbox" :aria-label="ariaLabel" :style="menuStyle" @pointerdown.prevent>
        <div v-for="(option, index) in options" :id="`${id}-option-${index}`" :key="option.value" class="select-option"
          :class="{ 'is-active': active === index, 'is-selected': option.value === modelValue }" role="option"
          :aria-selected="option.value === modelValue" :data-index="index" @pointermove="active = index" @click.stop="choose(index)">
          <span>{{ option.label }}</span><svg v-if="option.value === modelValue" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m4 10 4 4 8-8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>
        <p v-if="!options.length" class="select-empty">暂无可选分类</p>
      </div>
    </Teleport>
  </div>
</template>
