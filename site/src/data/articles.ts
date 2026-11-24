export const articles = Object.values(
  import.meta.glob('../../../data/news/*.json', { eager: true, import: 'default' })
).filter((item: any) => item.status === 'published') as any[];
