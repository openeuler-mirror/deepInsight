const routes = [
  {
    path: '/',
    component: '@/layouts',
    layout: false,
    wrappers: ['@/wrappers/auth'],
    routes: [
      { path: '/', redirect: '/research' },
      {
        path: '/research',
        component: '@/pages/research',
      },
    ],
  },
  {
    path: '/*',
    component: '@/pages/404',
    layout: false,
  },
];

export default routes;
