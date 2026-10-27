(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // 1. Atmosphere 3D Mood Lighting Controller
  const moodButtons = document.querySelectorAll('.btn-mood');
  const moodStatus = document.getElementById('mood-status');
  const moodNames = {
    cyan: 'Coastal Cyan',
    gold: 'Sunset Gold',
    amber: 'Candlelight Amber',
  };

  moodButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const mood = btn.dataset.mood;
      moodButtons.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      document.body.setAttribute('data-mood', mood);
      if (moodStatus && moodNames[mood]) {
        moodStatus.textContent = moodNames[mood];
      }
    });
  });

  // 2. 3D Hero Perspective Mouse & Parallax Tracking
  const scene = document.querySelector('#scene');
  const wrap = document.querySelector('.scene-wrap');
  const heroPlatter = document.querySelector('.hero-platter-3d');
  const dashCard = document.querySelector('.dashboard-card');

  if (scene && wrap && !reduced) {
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

    // Mobile Gyroscope 3D Tilt
    if (window.DeviceOrientationEvent) {
      window.addEventListener('deviceorientation', (e) => {
        if (e.gamma !== null && e.beta !== null) {
          mouseX = Math.max(-0.5, Math.min(0.5, e.gamma / 50));
          mouseY = Math.max(-0.5, Math.min(0.5, (e.beta - 45) / 50));
        }
      }, { passive: true });
    }

    const animateScene = () => {
      currentX += (mouseX - currentX) * 0.08;
      currentY += (mouseY - currentY) * 0.08;

      const rotX = 6 - currentY * 18;
      const rotY = -8 + currentX * 24;

      scene.style.transform = `translate(-50%, -50%) rotateX(${rotX}deg) rotateY(${rotY}deg)`;

      // Multi-layer depth parallax
      if (heroPlatter) {
        const pX = currentX * 26;
        const pY = currentY * 26;
        heroPlatter.style.transform = `translateZ(95px) translate(${pX}px, ${pY}px)`;
      }
      if (dashCard) {
        const cX = -currentX * 12;
        const cY = -currentY * 12;
        dashCard.style.transform = `translateZ(55px) translate(${cX}px, ${cY}px)`;
      }

      requestAnimationFrame(animateScene);
    };

    requestAnimationFrame(animateScene);
  }

  // 3. Interactive 3D Card Tilt with Specular Glare Sheen
  const cards = document.querySelectorAll('.card-3d');
  if (!reduced && window.matchMedia('(min-width: 768px)').matches) {
    cards.forEach((card) => {
      const glare = card.querySelector('.card-glare');

      card.addEventListener('pointermove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;

        const tiltX = (0.5 - y) * 16;
        const tiltY = (x - 0.5) * 16;

        card.style.transform = `perspective(1000px) translateY(-8px) rotateX(${tiltX}deg) rotateY(${tiltY}deg)`;

        if (glare) {
          glare.style.background = `radial-gradient(circle at ${x * 100}% ${y * 100}%, rgba(255, 255, 255, 0.22) 0%, transparent 60%)`;
          glare.style.opacity = '1';
        }
      });

      card.addEventListener('pointerleave', () => {
        card.style.transform = '';
        if (glare) {
          glare.style.opacity = '0';
        }
      });
    });
  }

  // 4. GSAP Scroll Animations
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

  // Ambience and Feature cards reveal
  gsap.utils.toArray('.ambience-hero-card, .ambience-side-card, .capability-card, .special-card, .testimonial-card, .closing-card').forEach((el, i) => {
    gsap.from(el, {
      y: 30,
      opacity: 0,
      duration: 0.65,
      delay: (i % 3) * 0.1,
      scrollTrigger: { trigger: el, start: 'top 88%' },
    });
  });
})();
