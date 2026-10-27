(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // 3D Hero Perspective Mouse Tracking
  const scene = document.querySelector('#scene');
  const wrap = document.querySelector('.scene-wrap');

  if (scene && wrap && !reduced && window.matchMedia('(min-width: 900px)').matches) {
    let mouseX = 0, mouseY = 0;
    let currentX = 0, currentY = 0;

    wrap.addEventListener('pointermove', (e) => {
      const rect = wrap.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      mouseX = x;
      mouseY = y;
    });

    wrap.addEventListener('pointerleave', () => {
      mouseX = 0;
      mouseY = 0;
    });

    const animateScene = () => {
      currentX += (mouseX - currentX) * 0.08;
      currentY += (mouseY - currentY) * 0.08;

      const rotX = 6 - currentY * 16;
      const rotY = -8 + currentX * 22;

      scene.style.transform = `translate(-50%, -50%) rotateX(${rotX}deg) rotateY(${rotY}deg)`;
      requestAnimationFrame(animateScene);
    };

    requestAnimationFrame(animateScene);
  }

  // Interactive 3D Card Tilt
  const cards = document.querySelectorAll('.card-3d');
  if (!reduced && window.matchMedia('(min-width: 768px)').matches) {
    cards.forEach((card) => {
      card.addEventListener('pointermove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width - 0.5;
        const y = (e.clientY - rect.top) / rect.height - 0.5;

        const tiltX = -y * 8;
        const tiltY = x * 8;

        card.style.transform = `perspective(1000px) translateY(-5px) rotateX(${tiltX}deg) rotateY(${tiltY}deg)`;
      });

      card.addEventListener('pointerleave', () => {
        card.style.transform = '';
      });
    });
  }

  // GSAP Animations
  if (!window.gsap || reduced) return;
  if (window.ScrollTrigger) gsap.registerPlugin(ScrollTrigger);

  // Hero entrance
  gsap.from('.hero-copy > *', {
    y: 30,
    opacity: 0,
    stagger: 0.12,
    duration: 0.85,
    ease: 'power3.out',
  });

  gsap.from('.scene', {
    opacity: 0,
    scale: 0.85,
    x: 40,
    duration: 1.1,
    ease: 'power3.out',
    delay: 0.2,
  });

  // Section titles reveal
  gsap.utils.toArray('.section-head').forEach((el) => {
    gsap.from(el, {
      y: 35,
      opacity: 0,
      duration: 0.8,
      scrollTrigger: { trigger: el, start: 'top 85%' },
    });
  });

  // Ambience and Capability cards reveal
  gsap.utils.toArray('.ambience-hero-card, .ambience-side-card, .capability-card, .ops-panel, .closing-card').forEach((el, i) => {
    gsap.from(el, {
      y: 30,
      opacity: 0,
      duration: 0.65,
      delay: (i % 4) * 0.1,
      scrollTrigger: { trigger: el, start: 'top 88%' },
    });
  });

  // Counter animation
  const counts = document.querySelectorAll('[data-count]');
  counts.forEach((count) => {
    if (window.ScrollTrigger) {
      ScrollTrigger.create({
        trigger: count,
        start: 'top 88%',
        once: true,
        onEnter: () => {
          gsap.to({ val: 0 }, {
            val: Number(count.dataset.count),
            duration: 1.6,
            ease: 'power2.out',
            onUpdate() {
              count.textContent = Math.round(this.targets()[0].val);
            },
          });
        },
      });
    }
  });
})();
