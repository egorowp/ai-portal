import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://egorowp.github.io/ai-portal',
  base: '/ai-portal',
  integrations: [sitemap()],
});
