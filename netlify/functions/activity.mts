import { activity } from './_shared/activity.mjs';

export default async (request: Request) => activity(request, Netlify.env);
export const config = { path: '/api/activity' };
