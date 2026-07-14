import { defineCollection, z } from 'astro:content';

const landingPages = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    description: z.string(),
    blocks: z.array(z.record(z.any())).default([]),
  }),
});

export const collections = { landingPages };
