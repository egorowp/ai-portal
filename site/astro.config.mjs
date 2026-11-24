import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://YOUR_GITHUB_USERNAME.github.io/ai-portal',
  base: '/ai-portal',
  integrations: [sitemap()],
});
