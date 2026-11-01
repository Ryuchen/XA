/** Current assigned providers; released rows remain available in order history. */
export function currentProviderNames(order: any): string {
  if (Array.isArray(order?.providers) && order.providers.length) {
    return order.providers.filter((provider: any) => !provider.released_at)
      .map((provider: any) => provider.provider_name).filter(Boolean).join('、') || '—'
  }
  return order?.provider_name || '—'
}

export function currentProviderIdentities(order: any): any[] {
  if (Array.isArray(order?.providers) && order.providers.length) return order.providers.filter((person: any) => !person.released_at)
  return order?.provider_name || order?.provider_username || order?.provider_no ? [order] : []
}

/** Add available identity only when names within the same order are ambiguous. */
export function providerListText(order: any): string {
  const people = currentProviderIdentities(order)
  const names = people.map(person => person.provider_name || '陪玩')
  if (new Set(names).size === names.length) return currentProviderNames(order)
  return people.map((person, index) => {
    if (names.indexOf(names[index]) === names.lastIndexOf(names[index])) return names[index]
    const sameNumber = person.provider_no && people.some((other, otherIndex) => otherIndex !== index && names[otherIndex] === names[index] && other.provider_no === person.provider_no)
    const account = person.provider_username ? `${person.provider_identity_source === 'LEGACY' ? '旧账号' : '账号'} ${person.provider_username}` : ''
    const identity = person.provider_no ? [`编号 ${person.provider_no}`, ...(sameNumber ? [account || '身份待核对'] : [])].join(' · ')
      : account || '身份待核对'
    return `${names[index]}（${identity}）`
  }).join('、')
}
