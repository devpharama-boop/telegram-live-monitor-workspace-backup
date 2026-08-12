---
category: projects
title: Firebase Db Structure
tags: ["firebase", "firebase-db-structure", "possessions", "project-structure", "realtime-db"]
updated: "2026-08-11T13:24:33Z"
source: agent_extract
---

Firebase Realtime Database structure:
- `admin/upiId`: stores the admin's UPI ID
- `sms/`: each SMS record contains sender, body, amount, status, timestamp, utr
- `payments/`: each payment record contains paymentId, orderId, upiId, amount, note, customerName, status, upiLink, qrData, createdAt, verifiedAt, utr
