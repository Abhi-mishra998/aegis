const _bus = new Map()

export const eventBus = {
  on(event, handler) {
    if (!_bus.has(event)) _bus.set(event, new Set())
    _bus.get(event).add(handler)
    return () => _bus.get(event)?.delete(handler)
  },
  emit(event, data) {
    _bus.get(event)?.forEach(h => h(data))
    _bus.get('*')?.forEach(h => h({ event, data }))
  },
  off(event, handler) {
    _bus.get(event)?.delete(handler)
  },
}
