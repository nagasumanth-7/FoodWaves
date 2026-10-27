const reservationDate = document.querySelector("#date");
const reservationTime = document.querySelector("#time");
const guestCount = document.querySelector("#guests");
const availabilityStatus = document.querySelector("#status");

reservationDate.min = new Date().toISOString().slice(0, 10);

function checkAvailability() {
  if (!reservationDate.value || !reservationTime.value || !guestCount.value) {
    return;
  }

  const isPopularTime = ["7:30 PM", "8:30 PM"].includes(reservationTime.value);
  availabilityStatus.className = isPopularTime ? "status wait" : "status ok";
  availabilityStatus.textContent = isPopularTime
    ? "This time is popular. Your table can be ready after a 25–35 minute wait."
    : "Good news — a table is available for your selected time.";
}

[reservationDate, reservationTime, guestCount].forEach((field) => {
  field.addEventListener("change", checkAvailability);
});

document.querySelector("#book").addEventListener("submit", (event) => {
  event.preventDefault();
  checkAvailability();
});
